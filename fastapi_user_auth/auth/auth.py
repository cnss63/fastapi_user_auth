import asyncio
import functools
import inspect
from collections.abc import Coroutine
from typing import Type, Any, TypeVar, Optional, Sequence, Tuple, Union, Callable, Generic
from fastapi import FastAPI, HTTPException, Depends, Form
from fastapi.security import OAuth2PasswordBearer
from fastapi.security.utils import get_authorization_scheme_param
from fastapi_amis_admin.crud.base import RouterMixin
from fastapi_amis_admin.crud.schema import BaseApiOut
from fastapi_amis_admin.crud.utils import schema_create_by_schema
from fastapi_amis_admin.utils.db import SqlalchemyAsyncClient
from fastapi_amis_admin.utils.functools import cached_property
from passlib.context import CryptContext
from pydantic import BaseModel, SecretStr
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.authentication import AuthenticationBackend
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import HTTPConnection, Request
from starlette.responses import RedirectResponse, Response
from starlette.websockets import WebSocket
from fastapi_user_auth.auth.models import Role, UserRoleLink
from .backends.base import BaseTokenStore
from .backends.db import DbTokenStore
from .models import BaseUser, User
from .schemas import UserLoginOut

_UserModelT = TypeVar("_UserModelT", bound=BaseUser)


class AuthBackend(AuthenticationBackend, Generic[_UserModelT]):

    def __init__(self, auth: "Auth", token_store: BaseTokenStore):
        self.auth = auth
        self.token_store = token_store

    @staticmethod
    def get_user_token(request: Request) -> Optional[str]:
        authorization: str = request.headers.get("Authorization") or request.cookies.get("Authorization")
        scheme, token = get_authorization_scheme_param(authorization)
        if not authorization or scheme.lower() != "bearer":
            return None
        return token

    async def authenticate(self, request: Request) -> Tuple["Auth", Optional[_UserModelT]]:
        if request.scope.get('auth'):  # 防止重复授权
            return request.scope.get('auth'), request.scope.get('user')
        request.scope["auth"], request.scope["user"] = self.auth, None
        token = self.get_user_token(request)
        if not token:
            return self.auth, None
        token_data = await self.token_store.read_token(token)
        if token_data is not None:
            request.scope["user"]: _UserModelT = await self.auth.get_user_by_username(token_data.username)
        return request.auth, request.user

    def attach_middleware(self, app: FastAPI):
        app.add_middleware(AuthenticationMiddleware, backend=self)  # 添加auth中间件


class Auth(Generic[_UserModelT]):
    user_model: Type[_UserModelT] = None
    db: SqlalchemyAsyncClient = None
    backend: AuthBackend[_UserModelT] = None

    def __init__(self, db: SqlalchemyAsyncClient,
                 token_store: BaseTokenStore = None,
                 user_model: Type[_UserModelT] = User,
                 pwd_context: CryptContext = CryptContext(schemes=["bcrypt"], deprecated="auto")
                 ):
        self.user_model = user_model or self.user_model
        assert self.user_model, 'user_model is None'
        self.db = db or self.db
        token_store = token_store or DbTokenStore(self.db)
        self.backend = self.backend or AuthBackend(self, token_store)
        self.pwd_context = pwd_context

    async def get_user_by_username(self, username: str) -> Optional[_UserModelT]:
        async with self.db.session_maker() as session:
            user = await session.scalar(select(self.user_model).where(self.user_model.username == username))
        return user

    async def get_user_by_whereclause(self, *whereclause: Any) -> Optional[_UserModelT]:
        async with self.db.session_maker() as session:
            user = await session.scalar(select(self.user_model).where(*whereclause))
        return user

    async def authenticate_user(self, username: str, password: Union[str, SecretStr]) -> Optional[_UserModelT]:
        user = await self.get_user_by_username(username)
        pwd = password.get_secret_value() if isinstance(password, SecretStr) else password
        pwd2 = user.password.get_secret_value() if isinstance(user.password, SecretStr) else user.password
        if user and self.pwd_context.verify(pwd, pwd2):  # 用户存在 且 密码验证通过
            return user
        return None

    def requires(self,
                 roles: Union[str, Sequence[str]] = None,
                 groups: Union[str, Sequence[str]] = None,
                 permissions: Union[str, Sequence[str]] = None,
                 status_code: int = 403,
                 redirect: str = None,
                 response: Optional[Union[Response, bool]] = None,
                 ) -> Callable:  # sourcery no-metrics

        async def has_requires(conn: HTTPConnection):
            # todo websocket support
            await self.backend.authenticate(conn)  # type:ignore
            if not conn.user:
                return False
            async with self.db.session_maker() as session:
                if groups:
                    groups_list = [groups] if isinstance(groups, str) else list(groups)
                    if not await conn.user.has_group(groups_list, session=session):
                        return False
                if roles:
                    roles_list = [roles] if isinstance(roles, str) else list(roles)
                    if not await conn.user.has_role(roles_list, session=session):
                        return False
                if permissions:
                    permissions_list = [permissions] if isinstance(permissions, str) else list(permissions)
                    if not await conn.user.has_permission(permissions_list, session=session):
                        return False
            return True

        async def depend(request: Request):
            if not await has_requires(request):
                if redirect is not None:
                    return RedirectResponse(
                        url=request.url_for(redirect), status_code=303
                    )
                if response is not None:
                    return response
                raise HTTPException(status_code=status_code)
            return True

        def decorator(func: Callable = None) -> Union[Callable, Coroutine]:
            if func is None:
                return depend
            if isinstance(func, Request):
                return depend(func)
            sig = inspect.signature(func)
            for idx, parameter in enumerate(sig.parameters.values()):
                if parameter.name == "request" or parameter.name == "websocket":
                    type_ = parameter.name
                    break
            else:
                raise Exception(
                    f'No "request" or "websocket" argument on function "{func}"'
                )

            if type_ == "websocket":
                # Handle websocket functions. (Always async)
                @functools.wraps(func)
                async def websocket_wrapper(
                        *args: Any, **kwargs: Any
                ) -> None:
                    websocket = kwargs.get("websocket", args[idx] if args else None)
                    assert isinstance(websocket, WebSocket)
                    if not await has_requires(websocket):
                        await websocket.close()
                    else:
                        await func(*args, **kwargs)

                return websocket_wrapper

            elif asyncio.iscoroutinefunction(func):
                # Handle async request/response functions.
                @functools.wraps(func)
                async def async_wrapper(
                        *args: Any, **kwargs: Any
                ) -> Response:
                    request = kwargs.get("request", args[idx] if args else None)
                    assert isinstance(request, Request)
                    response = await depend(request)
                    if response is True:
                        return await func(*args, **kwargs)
                    return response

                return async_wrapper

            else:
                # Handle sync request/response functions.
                @functools.wraps(func)
                def sync_wrapper(*args: Any, **kwargs: Any) -> Response:
                    request = kwargs.get("request", args[idx] if args else None)
                    assert isinstance(request, Request)
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    response = loop.run_until_complete(loop.create_task(depend(request)))
                    if response is True:
                        return func(*args, **kwargs)
                    return response

                return sync_wrapper

        return decorator

    async def create_role_user(self, role_key: str = 'admin') -> User:
        async with self.db.session_maker() as session:
            session: AsyncSession
            # create admin role
            role = await session.scalar(select(Role).where(Role.key == role_key))
            if not role:
                role = Role(key=role_key, name=f'{role_key} role')
                session.add(role)
                await session.commit()
                await session.refresh(role)

            # create admin user
            user = await session.scalar(
                select(User).join(UserRoleLink).where(UserRoleLink.role_id == role.id))
            if not user:
                user = User(
                    username=role_key,
                    password=self.pwd_context.hash(role_key),
                    email=f'{role_key}@amis.work', # type:ignore
                    roles=[role],
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
        return user


class AuthRouter(RouterMixin):
    auth: Auth = None
    schema_user_login_out: Type[UserLoginOut] = UserLoginOut
    router_prefix = '/auth'
    schema_user_info: Type[BaseModel] = None

    def __init__(self, auth: Auth = None):
        self.auth = auth or self.auth
        assert self.auth, 'auth is None'
        RouterMixin.__init__(self)
        self.router.dependencies.insert(0, Depends(self.auth.backend.authenticate))
        self.schema_user_info = self.schema_user_info \
                                or schema_create_by_schema(self.auth.user_model, 'UserInfo', exclude={'password'})

        self.router.add_api_route('/userinfo', self.router_userinfo, methods=["GET"], description='用户信息',
                                  dependencies=None, response_model=BaseApiOut[self.schema_user_info])
        # oauth2
        self.router.dependencies.append(
            Depends(self.OAuth2(tokenUrl=f"{self.router_path}/gettoken", auto_error=False)))
        self.router.add_api_route('/gettoken', self.router_token, methods=["POST"], description='OAuth2 Token',
                                  response_model=BaseApiOut[self.schema_user_login_out])

    @cached_property
    def router_path(self) -> str:
        return self.router.prefix

    @property
    def router_userinfo(self):
        @self.auth.requires()
        async def userinfo(request: Request):
            return BaseApiOut(data=request.user)

        return userinfo

    @property
    def router_token(self):
        async def oauth_token(request: Request,
                              response: Response,
                              username: str = Form(...),
                              password: str = Form(...)
                              ):
            if request.scope.get('user') is None:
                request.scope['user'] = await request.auth.authenticate_user(username=username, password=password)
            if request.scope.get('user') is None:
                return BaseApiOut(status=-1, msg='Incorrect username or password!')
            token_info = self.schema_user_login_out.parse_obj(request.user)
            token_info.access_token = await request.auth.backend.token_store.write_token(request.user.dict())
            response.set_cookie('Authorization', f'bearer {token_info.access_token}')
            return BaseApiOut(data=token_info)

        return oauth_token

    class OAuth2(OAuth2PasswordBearer):
        async def __call__(self, request: Request) -> Optional[str]:
            return AuthBackend.get_user_token(request)

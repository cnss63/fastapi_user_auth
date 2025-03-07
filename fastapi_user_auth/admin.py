from typing import Dict, Any, Type, Callable
from fastapi import Depends, HTTPException
from fastapi_amis_admin.amis.components import ActionType, Action, ButtonToolbar, Form, Html, Grid, Page, Horizontal, \
    PageSchema
from fastapi_amis_admin.amis.constants import LevelEnum, DisplayModeEnum
from fastapi_amis_admin.amis_admin.admin import FormAdmin, ModelAdmin
from fastapi_amis_admin.crud.schema import BaseApiOut
from pydantic import BaseModel
from sqlalchemy import insert
from starlette import status
from starlette.requests import Request
from starlette.responses import Response
from fastapi_user_auth.auth import Auth
from fastapi_user_auth.auth.models import BaseUser, User, Group, Permission, Role
from fastapi_user_auth.auth.schemas import UserLoginOut


def attach_page_head(page: Page) -> Page:
    page.body = [Html(
        html='<div style="display: flex; justify-content: center; align-items: center; margin: 96px 0px 8px;"><img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAFgAAABYBAMAAACDuy0HAAAABGdBTUEAALGPC/xhBQAAAAFzUkdCAK7OHOkAAAAkUExURQuQ9A+M7hCM7xCM7hCM7hCM7w+M7hCM7hCM7xCM7w+M7hCM7p2RGX4AAAALdFJOUwHxFtl5wi6YRq9emx6XmgAAA0VJREFUSMeNV89rGkEU3m6sVXNKK6TEiweLhJ56C/HikqN3EbwEihRv7RZKIRchBHq2EChesvgXiC5mmX+u+2Pem/fezLo7Bw/6OTPv+773YzzPsVp/97+9mqs1Ukrd1MTOVbamdbD+gyrW9xrgQGOP/Wrso8bG99XYpcbuX6qxQ43dhdXYTk+D1zUJVpTkaFaGbW40tgvfLErP8IOeIC2PYOwEbyVpjfzP+9UJgpG0jo4gsdW5BtJA5eZcyRBgDZQgDS1iOxAJvpEWsWlHgq9ktHoRSvB6SNoCIoCoV5aDkbQhnr+U28D1dmNGcLpSsZ/4Bb/CNqEzAthq6iTCR4vkR/tzcu5EUg8RJDqCNkTU95pSp0ifFAMBAE4z+J3QaSEtQk3+lus0lBZBXrNozww4DaFh5RWEdMiiPSei7v+MDMHcuEW0GXg34k4wFllyuTNw3OHoS2lc0DYHewyd3AvZsYp8zsFGR+Kw1heqtNk5PbEn7YTGvUUvZzsnNJZQEow++GbAWrtpaekdxgTsRfRIu4pcFOAjbnaQeUUtwsHNf31ZesekDsaFgrKeoUVYtAVY1jMkDS0S5WJpIyWsgzxIggs7xeA62psiaREtAeysdqb6fVKieoK4ibHQzOOsIcFom4SYYs2uYedgrt21Ev0348nk4EZRsKmCY7OZlYPoCggfS8VwJplR2slZn1Il7QMPvTN+w4sd3d1ZhWe4Mwm567TIc16LYqupzBzN49bjYFPz1padstMKcLsvG6Em0P9B86oAn3etFrvifOaya7AxYkCLjPgrgNNYhY8v+9h34FJt9HMoB47kTjaldi7Km/TzwnaYKFz+BMEmBxs9huV5ddRtIpE1wBp/suIPYsh8s8cfpV69ljxxW1qm3xu+ZEU0ZRqiTmloKGet3b1IPj86sypzkMnBCU+MQPaHzkikOvkR/WXKCgYgZSfXOpQNcCo8NVAJQafOUY1/O3IPdgPHeSyv3OPl2BrKHE+FJ0lgIGWnK+I/bq35j60JnbqQ4JJnBRaxKzfBJQ+E2UC5qCwRoecmWJRNnoPd0++rBcUmVW+xR1LpV5XPwUBZWp5Ab8TMWOdZ+lzvEduZ18eme//88Mv1/X8KQ6zq3Tt8/QAAAABJRU5ErkJggg==" alt="logo" style="margin-right: 8px; width: 48px;"><span style="font-size: 32px; font-weight: bold;">Amis Admin</span></div><div style="width: 100%; text-align: center; color: rgba(0, 0, 0, 0.45); margin-bottom: 40px;">Amis是一个低代码前端框架，可以减少页面开发工作量，极大提升效率</div>'),
        Grid(columns=[{"body": [page.body], "lg": 2, "md": 4, "valign": "middle"}], align='center',
             valign='middle')]
    return page


class UserLoginFormAdmin(FormAdmin):
    page = Page(title='用户登录')
    page_path = '/login'
    page_parser_mode = 'html'
    schema: Type[BaseModel] = None
    schema_submit_out: Type[UserLoginOut] = None
    page_schema = None

    async def handle(self, request: Request,
                     data: "self.schema",  # type:ignore
                     **kwargs) -> BaseApiOut["self.schema_submit_out"]:  # type:ignore
        if request.user:
            return BaseApiOut(code=1, msg='用户已登录', data=self.schema_submit_out.parse_obj(request.user))
        user = await request.auth.authenticate_user(username=data.username, password=data.password)  # type:ignore
        if not user:
            return BaseApiOut(status=-1, msg='用户名或密码不正确!')
        if not user.is_active:
            return BaseApiOut(status=-2, msg='用户状态未激活!')

        token_info = self.schema_submit_out.parse_obj(user)
        auth: Auth = request.auth
        token_info.access_token = await auth.backend.token_store.write_token(user.dict())
        return BaseApiOut(code=0, data=token_info)

    @property
    def route_submit(self):
        async def route(response: Response, result: BaseApiOut = Depends(super().route_submit)):
            if result.status == 0 and result.code == 0:  # 登录成功,设置用户信息
                response.set_cookie('Authorization', 'bearer ' + result.data.access_token)
            return result

        return route

    async def get_form(self, request: Request) -> Form:
        form = await super().get_form(request)
        form.update_from_kwargs(title='', mode=DisplayModeEnum.horizontal, submitText="登录",
                                actionsClassName="no-border m-none p-none", panelClassName="", wrapWithPanel=True,
                                horizontal=Horizontal(left=3, right=9),
                                actions=[
                                    ButtonToolbar(buttons=[
                                        ActionType.Link(actionType='link', link=self.router_path + '/reg',
                                                        label='注册'),
                                        Action(actionType='submit', label='登录', level=LevelEnum.primary)])]
                                )
        form.redirect = request.query_params.get('redirect') or '/'
        return form

    async def get_page(self, request: Request) -> Page:
        page = await super().get_page(request)
        return attach_page_head(page)

    @property
    def route_page(self) -> Callable:
        async def route(request: Request, result=Depends(super().route_page)):
            if request.user:
                raise HTTPException(status_code=status.HTTP_307_TEMPORARY_REDIRECT, detail='already logged in',
                                    headers={'location': request.query_params.get('redirect') or '/'})
            return result

        return route

    async def has_page_permission(self, request: Request) -> bool:
        return True


class UserRegFormAdmin(FormAdmin):
    user_model: Type[BaseUser] = User
    page = Page(title='用户注册')
    page_path = '/reg'
    page_parser_mode = 'html'
    schema: Type[BaseModel] = None
    schema_submit_out: Type[UserLoginOut] = None
    page_schema = None

    async def handle(self, request: Request,
                     data: "self.schema",  # type:ignore
                     **kwargs) -> BaseApiOut["self.schema_submit_out"]:  # type:ignore
        user = await request.auth.get_user_by_username(data.username)
        if user:
            return BaseApiOut(status=-1, msg='用户名已注册!', data=None)
        user = await  request.auth.get_user_by_whereclause(self.user_model.email == data.email)
        if user:
            return BaseApiOut(status=-2, msg='邮箱已注册!', data=None)
        user = self.user_model.parse_obj(data)
        values = user.dict(exclude={'id', 'password'})
        values['password'] = request.auth.pwd_context.hash(user.password.get_secret_value())  # 密码hash保存
        stmt = insert(self.user_model).values(values)
        try:
            async with request.auth.db.session_maker() as session:
                result = await session.execute(stmt)
                if result.rowcount:  # type: ignore
                    await session.commit()
                    user.id = result.lastrowid  # type: ignore
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR) from e
        # 注册成功,设置用户信息
        token_info = self.schema_submit_out.parse_obj(user)
        auth: Auth = request.auth
        token_info.access_token = await auth.backend.token_store.write_token(user.dict())
        return BaseApiOut(code=0, msg='注册成功!', data=token_info)

    @property
    def route_submit(self):
        async def route(response: Response, result: BaseApiOut = Depends(super().route_submit)):
            if result.status == 0 and result.code == 0:  # 登录成功,设置用户信息
                response.set_cookie('Authorization', f'bearer {result.data.access_token}' )
            return result

        return route

    async def get_form(self, request: Request) -> Form:
        form = await super().get_form(request)
        form.redirect = request.query_params.get('redirect') or '/'
        form.update_from_kwargs(title='', mode=DisplayModeEnum.horizontal, submitText="注册",
                                actionsClassName="no-border m-none p-none", panelClassName="", wrapWithPanel=True,
                                horizontal=Horizontal(left=3, right=9),
                                actions=[
                                    ButtonToolbar(buttons=[
                                        ActionType.Link(actionType='link', link=self.router_path + '/login',
                                                        label='登录'),
                                        Action(actionType='submit', label='注册', level=LevelEnum.primary)])]
                                )

        return form

    async def get_page(self, request: Request) -> Page:
        page = await super().get_page(request)
        return attach_page_head(page)

    async def has_page_permission(self, request: Request) -> bool:
        return True


class UserAdmin(ModelAdmin):
    group_schema = None
    page_schema = PageSchema(label='用户管理', icon='fa fa-user')
    model: Type[BaseUser] = User
    exclude = ['password']
    link_model_fields = [User.roles, User.groups]
    search_fields = [User.username]

    async def on_create_pre(self, request: Request, obj, **kwargs) -> Dict[str, Any]:
        data = await super(UserAdmin, self).on_create_pre(request, obj, **kwargs)
        data['password'] = request.auth.pwd_context.hash(data['password'])  # 密码hash保存
        return data

    async def on_update_pre(self, request: Request, obj, **kwargs) -> Dict[str, Any]:
        data = await super(UserAdmin, self).on_update_pre(request, obj, **kwargs)
        password = data.get('password')
        if password:
            data['password'] = request.auth.pwd_context.hash(data['password'])  # 密码hash保存
        return data


class RoleAdmin(ModelAdmin):
    group_schema = None
    page_schema = PageSchema(label='角色管理', icon='fa fa-group')
    model = Role
    link_model_fields = [Role.permissions]
    readonly_fields = ['key']


class GroupAdmin(ModelAdmin):
    group_schema = None
    page_schema = PageSchema(label='用户组管理', icon='fa fa-group')
    model = Group
    link_model_fields = [Group.roles]
    readonly_fields = ['key']


class PermissionAdmin(ModelAdmin):
    group_schema = None
    page_schema = PageSchema(label='权限管理', icon='fa fa-lock')
    model = Permission
    readonly_fields = ['key']

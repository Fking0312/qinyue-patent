"""SPA 部分渲染辅助：根据 `X-Qy-Spa` 请求头切换全页 / 片段渲染。

前端通过同源 fetch 携带 `X-Qy-Spa: 1`，后端在视图里调用 `render_spa_or_full`
即可在普通跳转和 SPA 片段刷新两种模式下复用同一份模板数据。
"""

from flask import redirect, render_template, request, url_for

SPA_HEADER_NAME = "X-Qy-Spa"
SPA_HEADER_VALUE = "1"


def redirect_with_qy_toast(endpoint: str, message: str, variant: str = "success", **url_values):
    """
    POST 后统一用查询参数携带 Toast 文案（由前端 qyConsumeUrlToast 消费）。
    variant: success | warning | danger | info
    """
    return redirect(
        url_for(endpoint, qy_toast=message, qy_toast_variant=variant, **url_values)
    )


def wants_spa_fragment() -> bool:
    """当前请求是否来自 SPA 片段 fetch（依据 `X-Qy-Spa` 请求头判断）。"""
    return request.headers.get(SPA_HEADER_NAME) == SPA_HEADER_VALUE


def render_spa_or_full(
    *,
    full_template: str,
    inner_template: str,
    spa_endpoint: str,
    spa_document_title: str,
    **kwargs: object,
):
    """Return a full page or only the #qy-spa-main fragment for in-app navigation."""
    if wants_spa_fragment():
        return render_template(
            "partials/spa_frame.html",
            inner_template=inner_template,
            spa_endpoint=spa_endpoint,
            spa_document_title=spa_document_title,
            **kwargs,
        )
    return render_template(full_template, **kwargs)

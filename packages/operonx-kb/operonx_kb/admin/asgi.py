"""The admin ASGI app (track5 §16.2): Starlette routes over :mod:`operonx_kb.admin.views`.

Read-mostly, JSON everywhere except page images (PNG). An error is
``{"error": message}`` with a status: 404 for an unknown collection, document,
version, page or chunk; 400 for a malformed request or a label that does not
resolve; 501 for what this app was not configured or installed for; 500 for a
failed run, with that run's ``trace_id``.

==========================================  ==============================================
``GET  /.well-known/operonx-kb``            ``{"api": "operonx-kb/1", "collections", "answer", "rerank"}``
``GET  /collections``                       every collection with its spec, modes and counts
``GET  /collections/{c}``                   one of them
``GET  /collections/{c}/documents``         ``?q&status&page&size``: documents, newest first
``GET  /collections/{c}/health``            ``verify``: counts and problems
``POST /collections/{c}/query``             searches side by side and an answer, with trace ids
``POST /collections/{c}/eval-case``         a dataset row from a query and kept citations
``GET  /documents/{id}``                    versions and ingest log
``GET  /versions/{id}``                     pages and chunk occurrences with their boxes
``GET  /versions/{id}/tree``                the element tree
``GET  /versions/{id}/text``                the canonical text
``GET  /versions/{id}/pages/{n}``           a page's elements and chunks with their boxes
``GET  /versions/{id}/pages/{n}/image``     ``?scale``: the page as PNG (PDF only)
``GET  /chunks/{id}``                       ``?version``: the chunk inspector's data
==========================================  ==============================================
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Optional

from operonx.core.registry import ResourceHub

from operonx_kb.admin import views
from operonx_kb.admin.pages import PageImages
from operonx_kb.errors import CatalogError, FilterError, KBError, MissingExtraError
from operonx_kb.eval.labels import LabelError
from operonx_kb.kb import KnowledgeBase, QueryError

__all__ = ["kb_admin_app"]


def kb_admin_app(
    catalog: str = "kb_catalog:main",
    blobs: str = "kb_blob:main",
    *,
    llm: Optional[str] = None,
    reranker: Optional[str] = None,
    trace: Any = "project",
) -> Any:
    """The admin app for Studio's Knowledge tab, to mount as an ``asgi`` service.

    Args:
        catalog: ``kb_catalog`` resource key.
        blobs: ``kb_blob`` resource key.
        llm: The ``llm:`` resource name answers use; without it a query searches only.
        reranker: A ``reranking:`` resource name; without it a query cannot rerank.
        trace: Where query runs are traced (``Operon(trace=...)``). The default,
            ``"project"``, is the project's own ``[tracing]`` sinks, so a run's
            ``trace_id`` opens in Studio's Traces tab.

    The :class:`~operonx_kb.kb.KnowledgeBase` is made on the first request, from the
    resources the project loaded by then: ``operonx serve`` loads none for an asgi
    service, so the module that builds the app calls ``operonx.bootstrap()``.

    Raises:
        MissingExtraError: Starlette is not installed (the ``admin`` extra).
    """
    try:
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Route
    except ImportError as exc:
        raise MissingExtraError("The admin API", "admin", exc) from exc

    made: dict = {}
    lock = threading.Lock()

    def kb() -> KnowledgeBase:
        with lock:
            if "kb" not in made:
                try:
                    ResourceHub.instance()
                except RuntimeError as exc:  # no hub installed: say which, and how
                    raise views.Unavailable(
                        f"the admin app reads {catalog} and {blobs}, and no resources are loaded: "
                        "call operonx.bootstrap() in the module that builds the app (operonx serve "
                        "loads no resources for an asgi service)"
                    ) from exc
                made["kb"] = KnowledgeBase(catalog, blobs, trace=trace)
                made["pages"] = PageImages(made["kb"])
            return made["kb"]

    def fail(exc: Exception) -> JSONResponse:
        if isinstance(exc, views.NotFound):
            return JSONResponse({"error": str(exc)}, status_code=404)
        if isinstance(exc, (views.BadRequest, FilterError, LabelError, ValueError)):
            return JSONResponse({"error": str(exc)}, status_code=400)
        if isinstance(exc, (views.Unavailable, MissingExtraError)):
            return JSONResponse({"error": str(exc)}, status_code=501)
        if isinstance(exc, QueryError):
            return JSONResponse({"error": str(exc), "trace_id": exc.context.get("trace_id")},
                                status_code=500)  # fmt: skip
        if isinstance(exc, CatalogError):
            return JSONResponse({"error": str(exc)}, status_code=404)
        raise exc

    def read(fn: Callable[..., Any]) -> Callable[..., Any]:
        """A GET route: ``fn(request)`` in a worker thread (the catalog blocks), as JSON."""

        async def endpoint(request: Request) -> Any:
            try:
                got = await asyncio.to_thread(fn, request)
            except KBError as exc:
                return fail(exc)
            except ValueError as exc:
                return fail(exc)
            return got if isinstance(got, Response) else JSONResponse(got)

        return endpoint

    async def body_of(request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError as exc:
            raise views.BadRequest(f"the body is not JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise views.BadRequest("the body is a JSON object")
        return body

    def p(request: Request, name: str) -> str:
        return request.path_params[name]

    def number(request: Request, name: str, default: int) -> int:
        raw = request.query_params.get(name)
        try:
            return int(raw) if raw not in (None, "") else default
        except ValueError:
            raise views.BadRequest(f"{name} is a whole number, not {raw!r}") from None

    def image(request: Request) -> Response:
        kb()
        raw = request.query_params.get("scale") or "1.5"
        try:
            scale = float(raw)
        except ValueError:
            raise views.BadRequest(f"scale is a number, not {raw!r}") from None
        png = made["pages"].png(p(request, "version_id"), request.path_params["page_no"], scale)
        # a version's bytes never change: the image is immutable
        return Response(png, media_type="image/png",
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})  # fmt: skip

    async def query(request: Request) -> Any:
        try:
            got = await views.query(kb(), p(request, "collection"), await body_of(request),
                                    llm=llm, reranker=reranker)  # fmt: skip
        except (KBError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(got)

    async def eval_case(request: Request) -> Any:
        try:
            body = await body_of(request)
            got = await asyncio.to_thread(views.case, kb(), p(request, "collection"), body)
        except (KBError, ValueError) as exc:
            return fail(exc)
        return JSONResponse(got)

    routes = [
        Route("/.well-known/operonx-kb",
              read(lambda r: views.well_known(kb(), llm=llm, reranker=reranker))),
        Route("/collections", read(lambda r: views.collections(kb()))),
        Route("/collections/{collection}", read(lambda r: views.collection(kb(), p(r, "collection")))),
        Route("/collections/{collection}/documents",
              read(lambda r: views.documents(kb(), p(r, "collection"), q=r.query_params.get("q", ""),
                                             status=r.query_params.get("status", ""),
                                             page=number(r, "page", 1),
                                             size=number(r, "size", views.PAGE_SIZE)))),
        Route("/collections/{collection}/health", read(lambda r: views.health(kb(), p(r, "collection")))),
        Route("/collections/{collection}/query", query, methods=["POST"]),
        Route("/collections/{collection}/eval-case", eval_case, methods=["POST"]),
        Route("/documents/{document_id}", read(lambda r: views.document(kb(), p(r, "document_id")))),
        Route("/versions/{version_id}", read(lambda r: views.version(kb(), p(r, "version_id")))),
        Route("/versions/{version_id}/tree", read(lambda r: views.version_tree(kb(), p(r, "version_id")))),
        Route("/versions/{version_id}/text", read(lambda r: views.version_text(kb(), p(r, "version_id")))),
        Route("/versions/{version_id}/pages/{page_no:int}",
              read(lambda r: views.page(kb(), p(r, "version_id"), r.path_params["page_no"]))),
        Route("/versions/{version_id}/pages/{page_no:int}/image", read(image)),
        Route("/chunks/{chunk_id}",
              read(lambda r: views.chunk(kb(), p(r, "chunk_id"), r.query_params.get("version") or None))),
    ]  # fmt: skip
    return Starlette(routes=routes)

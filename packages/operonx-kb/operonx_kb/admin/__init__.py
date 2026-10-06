"""The admin API Studio's Knowledge tab talks to (track5 §16.2): ``operonx-kb/1``.

Studio never imports this package; it finds a KB by asking a served ``asgi``
service for ``GET <path>/.well-known/operonx-kb`` and speaks the HTTP contract
below. The contract is versioned (:data:`API`) so the two repos release
independently.

    import operonx
    from operonx.app import Service, asgi
    from operonx_kb.admin import kb_admin_app

    operonx.bootstrap()  # the project's resources.yaml: operonx serve loads none for an asgi service
    Service("kb_admin", asgi("/kb", port=8021), app=kb_admin_app(llm="gpt-4o-mini"))

:mod:`operonx_kb.admin.views` holds what each route answers (pure reads of the
catalog and blob store, and the query runs); :mod:`operonx_kb.admin.asgi` only
routes. The ASGI app needs the ``admin`` extra (Starlette, and pypdfium2 for
page images).
"""

from operonx_kb.admin.asgi import kb_admin_app
from operonx_kb.admin.views import API

__all__ = ["API", "kb_admin_app"]

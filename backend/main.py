"""
Aplicação principal FastAPI — Sistema de Orçamentos Públicos.
"""
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from dotenv import load_dotenv

# Carrega variáveis de ambiente
load_dotenv(Path(__file__).parent.parent / ".env")

from database.models import init_db
from routes.catmat import router as catmat_router
from routes.empresas import router as empresas_router
from routes.buscas import router as buscas_router
from routes.export import router as export_router
from routes.localidades import router as localidades_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicialização e encerramento da aplicação."""
    await init_db()
    print("[OK] Banco de dados inicializado")
    yield
    print("[FIM] Aplicacao encerrada")


app = FastAPI(
    title="Sistema de Orçamentos Públicos",
    description="Busca de fornecedores para processos de compra pública",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — permite acesso de qualquer origem (ajustar em produção)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Permitir EMBED (iframe) no Portal Gestor Público ─────────────────────────
# Para vincular este app como "ferramenta interna" de um site, o navegador só
# deixa embutir em iframe se o cabeçalho frame-ancestors permitir aquele domínio.
# Configure EMBED_ANCESTORS no .env com os domínios autorizados (separados por
# espaço). Padrão já inclui o Portal Gestor Público. 'self' permite o próprio app.
_EMBED_ANCESTORS = os.getenv(
    "EMBED_ANCESTORS",
    "'self' https://portalgestorpublico.com.br https://*.portalgestorpublico.com.br",
)

@app.middleware("http")
async def _permitir_embed(request, call_next):
    resp = await call_next(request)
    # frame-ancestors (CSP) é o mecanismo moderno; não enviamos X-Frame-Options
    # (que não aceita lista de domínios) para não conflitar.
    resp.headers["Content-Security-Policy"] = f"frame-ancestors {_EMBED_ANCESTORS};"
    return resp

# Rota de health check (antes do catch-all)
@app.get("/api/health")
async def health():
    return {"status": "ok", "versao": "1.0.0"}

# Rotas da API
app.include_router(catmat_router)
app.include_router(empresas_router)
app.include_router(buscas_router)
app.include_router(export_router)
app.include_router(localidades_router)

# Serve o frontend estatico (DEVE vir depois de todas as rotas de API)
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")

    @app.get("/")
    async def root():
        return FileResponse(str(frontend_path / "index.html"))

    # ── PWA: service worker e manifest servidos da RAIZ, com os tipos/headers
    #    corretos (necessário para instalar e funcionar offline). ──────────────
    @app.get("/sw.js")
    async def service_worker():
        return FileResponse(
            str(frontend_path / "sw.js"),
            media_type="application/javascript",
            headers={
                "Service-Worker-Allowed": "/",
                "Cache-Control": "no-cache",  # SW sempre revalida
            },
        )

    @app.get("/manifest.webmanifest")
    async def manifest():
        return FileResponse(
            str(frontend_path / "manifest.webmanifest"),
            media_type="application/manifest+json",
        )

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve arquivos estaticos ou retorna index.html para o SPA."""
        if full_path.startswith("api/"):
            from fastapi import HTTPException as _HTTPException
            raise _HTTPException(404, "Rota nao encontrada")
        file = frontend_path / full_path
        if file.exists() and file.is_file():
            return FileResponse(str(file))
        return FileResponse(str(frontend_path / "index.html"))


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    debug = os.getenv("DEBUG", "true").lower() == "true"
    uvicorn.run("main:app", host=host, port=port, reload=debug)

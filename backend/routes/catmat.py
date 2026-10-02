"""
Rotas da API para busca de CATMAT/CATSER.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from services.governo_api import buscar_catmat_catser
from services.gemini_api import ranquear_com_gemini

router = APIRouter(prefix="/api/catmat", tags=["CATMAT/CATSER"])


class BuscaCatRequest(BaseModel):
    tipo: str          # "material" | "servico"
    query: str
    descricao: Optional[str] = ""
    gemini_key: Optional[str] = ""  # chave opcional do usuário


@router.post("/buscar")
async def buscar_cat(req: BuscaCatRequest):
    """
    Busca CATMAT ou CATSER na API do governo e retorna até 10 sugestões.
    """
    tipo = req.tipo.lower().strip()
    if tipo not in ("material", "servico"):
        raise HTTPException(400, "Tipo deve ser 'material' ou 'servico'")

    if not req.query.strip():
        raise HTTPException(400, "Nome do item não pode ser vazio")

    try:
        resultados = await buscar_catmat_catser(
            tipo=tipo,
            query=req.query.strip(),
            descricao=req.descricao or "",
            max_resultados=10,
        )
    except Exception as e:
        raise HTTPException(502, f"Erro ao consultar API do governo: {str(e)}")

    # Se o usuário forneceu chave Gemini, melhora o ranqueamento
    if req.gemini_key and req.gemini_key.strip():
        tipo_cat = "CATMAT" if tipo == "material" else "CATSER"
        resultados = await ranquear_com_gemini(
            query=req.query,
            descricao=req.descricao or "",
            tipo=tipo_cat,
            candidatos=resultados,
            api_key=req.gemini_key.strip(),
        )

    return {"resultados": resultados, "total": len(resultados)}


@router.post("/recarregar-cache")
async def recarregar_cache(tipo: str = "material"):
    """
    Recarrega o cache local com dados frescos da API do governo.
    Use quando quiser atualizar os dados de CATMAT/CATSER.
    tipo: 'material' ou 'servico'
    """
    from services.governo_api import _popular_cache, CACHE_DB
    import sqlite3
    if tipo not in ("material", "servico"):
        raise HTTPException(400, "Tipo deve ser 'material' ou 'servico'")
    # Limpa o cache antigo para esse tipo
    tabela = "catmat" if tipo == "material" else "catser"
    try:
        con = sqlite3.connect(CACHE_DB)
        con.execute(f"DELETE FROM {tabela}")
        con.commit()
        con.close()
    except Exception:
        pass
    # Popula novamente
    await _popular_cache(tipo)
    con = sqlite3.connect(CACHE_DB)
    n = con.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
    con.close()
    return {"mensagem": f"Cache {tipo.upper()} recarregado com sucesso", "total_itens": n}


@router.get("/status-cache")
async def status_cache():
    """Retorna o estado atual do cache local."""
    from services.governo_api import CACHE_DB
    import sqlite3, os
    if not CACHE_DB.exists():
        return {"catmat": 0, "catser": 0, "tamanho_kb": 0}
    con = sqlite3.connect(CACHE_DB)
    n_mat = con.execute("SELECT COUNT(*) FROM catmat").fetchone()[0]
    n_ser = con.execute("SELECT COUNT(*) FROM catser").fetchone()[0]
    con.close()
    tamanho = os.path.getsize(CACHE_DB) // 1024
    return {"catmat": n_mat, "catser": n_ser, "tamanho_kb": tamanho}

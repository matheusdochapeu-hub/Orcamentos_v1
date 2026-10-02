"""
Rotas para salvar, listar e excluir buscas no SQLite.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import json
import aiosqlite
from database.models import DB_PATH

router = APIRouter(prefix="/api/buscas", tags=["Buscas Salvas"])


class SalvarBuscaRequest(BaseModel):
    nome_busca: Optional[str] = None
    tipo: str               # CATMAT | CATSER
    catcodigo: str
    catdescricao: str
    cidade: str
    estado: Optional[str] = ""
    raio_km: int
    tipo_busca: str
    empresas: list[dict]    # lista completa de empresas


@router.post("/salvar")
async def salvar_busca(req: SalvarBuscaRequest):
    """Salva uma busca completa no banco de dados."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO buscas
               (nome_busca, tipo, catcodigo, catdescricao, cidade, estado, raio_km, tipo_busca, total_empresas)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                req.nome_busca or f"{req.catdescricao[:40]} — {req.cidade}",
                req.tipo,
                req.catcodigo,
                req.catdescricao,
                req.cidade,
                req.estado or "",
                req.raio_km,
                req.tipo_busca,
                len(req.empresas),
            ),
        )
        busca_id = cursor.lastrowid

        # Salva empresas
        for emp in req.empresas:
            await db.execute(
                """INSERT INTO empresas_busca
                   (busca_id, place_id, nome, endereco, telefone, whatsapp, email, site, latitude, longitude, relevancia, dados_extra)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    busca_id,
                    emp.get("place_id", ""),
                    emp.get("nome", ""),
                    emp.get("endereco", ""),
                    emp.get("telefone", ""),
                    emp.get("whatsapp", ""),
                    emp.get("email", ""),
                    emp.get("site", ""),
                    emp.get("lat"),
                    emp.get("lng"),
                    emp.get("relevancia", 0),
                    json.dumps({
                        "avaliacao": emp.get("avaliacao_google"),
                        "tipos": emp.get("tipos", []),
                        "descricao": emp.get("descricao", ""),
                    }, ensure_ascii=False),
                ),
            )

        await db.commit()

    return {"id": busca_id, "mensagem": "Busca salva com sucesso"}


@router.get("/listar")
async def listar_buscas():
    """Lista todas as buscas salvas (resumo)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, nome_busca, tipo, catdescricao, cidade, raio_km, total_empresas, criado_em FROM buscas ORDER BY criado_em DESC"
        )
        rows = await cursor.fetchall()

    return {"buscas": [dict(r) for r in rows]}


@router.get("/{busca_id}")
async def obter_busca(busca_id: int):
    """Retorna uma busca completa com todas as empresas."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("SELECT * FROM buscas WHERE id = ?", (busca_id,))
        busca = await cursor.fetchone()
        if not busca:
            raise HTTPException(404, "Busca não encontrada")

        cursor2 = await db.execute(
            "SELECT * FROM empresas_busca WHERE busca_id = ? ORDER BY relevancia DESC",
            (busca_id,),
        )
        empresas = await cursor2.fetchall()

    return {
        "busca": dict(busca),
        "empresas": [dict(e) for e in empresas],
    }


@router.delete("/{busca_id}")
async def excluir_busca(busca_id: int):
    """Exclui uma busca e todas as empresas associadas."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM buscas WHERE id = ?", (busca_id,))
        await db.commit()
    return {"mensagem": "Busca excluída com sucesso"}

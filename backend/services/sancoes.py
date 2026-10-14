"""
Cruzamento com CEIS/CNEP (empresas sancionadas) — Portal da Transparência.

Em compra pública, é essencial não perder tempo com empresas IMPEDIDAS de
contratar. Este módulo marca, por CNPJ, se a empresa consta em:
  - CEIS: Cadastro de Empresas Inidôneas e Suspensas
  - CNEP: Cadastro Nacional de Empresas Punidas

A API do Portal da Transparência exige uma CHAVE GRATUITA (cadastro de e-mail em
portaldatransparencia.gov.br/api-de-dados/cadastrar-email) e tem limite de
requisições. Por isso este cruzamento é OPCIONAL:
  - Só é ativado se a env PORTAL_TRANSPARENCIA_KEY estiver definida.
  - Se não houver chave, ou se a consulta falhar, a busca segue normalmente
    (as empresas apenas não recebem o selo de verificação).

Resultado por empresa (campos adicionados ao schema):
  - sancionada: True/False
  - sancao_detalhe: texto curto (ex.: "CEIS: inidônea")
"""
from __future__ import annotations

import os
import asyncio

import httpx

BASE = "https://api.portaldatransparencia.gov.br/api-de-dados"
TIMEOUT = 20.0
MAX_VERIFICAR = 20  # limita nº de CNPJs verificados por busca (respeita rate limit)


def _key() -> str:
    return (os.getenv("PORTAL_TRANSPARENCIA_KEY") or "").strip()


def habilitado() -> bool:
    return bool(_key())


def _so_digitos(s) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


async def _consultar(client, caminho: str, cnpj: str) -> list:
    """Consulta um cadastro (ceis/cnep) por CNPJ sancionado."""
    try:
        r = await client.get(
            f"{BASE}/{caminho}",
            params={"cnpjSancionado": cnpj, "pagina": 1},
            headers={"chave-api-dados": _key(), "Accept": "application/json"},
        )
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def marcar_sancoes(empresas: list[dict]) -> list[dict]:
    """
    Para cada empresa (até MAX_VERIFICAR com CNPJ), verifica CEIS e CNEP e
    adiciona 'sancionada'/'sancao_detalhe'. Degrada em silêncio se não houver
    chave ou se a API falhar.
    """
    if not habilitado() or not empresas:
        return empresas

    alvos = [e for e in empresas if _so_digitos(e.get("cnpj"))][:MAX_VERIFICAR]
    if not alvos:
        return empresas

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for emp in alvos:
            cnpj = _so_digitos(emp["cnpj"])
            ceis, cnep = await asyncio.gather(
                _consultar(client, "ceis", cnpj),
                _consultar(client, "cnep", cnpj),
                return_exceptions=True,
            )
            detalhes = []
            if isinstance(ceis, list) and ceis:
                detalhes.append("CEIS (inidônea/suspensa)")
            if isinstance(cnep, list) and cnep:
                detalhes.append("CNEP (punida)")
            if detalhes:
                emp["sancionada"] = True
                emp["sancao_detalhe"] = " · ".join(detalhes)
            else:
                emp["sancionada"] = False
                emp["sancao_detalhe"] = ""
            await asyncio.sleep(0.2)  # respeita rate limit

    return empresas

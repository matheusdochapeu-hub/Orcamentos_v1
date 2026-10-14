"""
Provedor de empresas de ALTA CONFIANÇA via PNCP (Portal Nacional de
Contratações Públicas).

Ideia: em vez de "quais empresas têm atividade (CNAE) parecida?", perguntamos
"quais empresas JÁ VENDERAM este tipo de item ao poder público?". Se a empresa
ganhou/forneceu uma contratação do produto buscado, ela comprovadamente fornece
aquilo — precisão muito maior que o CNAE.

Fluxo:
  1. Busca textual no motor do PNCP (`/api/search`) pelo nome do item,
     filtrando por UF, trazendo contratações cujos itens casam com o termo.
  2. Extrai os CNPJs dos fornecedores dessas contratações.
  3. Enriquece cada CNPJ com endereço + telefone via MinhaReceita
     (consulta por CNPJ individual — parte estável da API).
  4. Marca cada empresa como "já forneceu este tipo de item" (alta confiança).

Tudo gratuito e sem chave. Como o PNCP é externo e seu contrato pode variar, a
extração é defensiva. Em qualquer falha, retorna [] (o agregador segue com a
Receita/CNAE) — este provedor é COMPLEMENTO de precisão, não pode derrubar a busca.

Config (.env):
  PNCP_SEARCH_URL=https://pncp.gov.br/api/search/   (padrão)
  PNCP_ENABLED=1                                     (0 desliga o PNCP)
"""
from __future__ import annotations

import os
import re
import asyncio

import httpx

from services.empresas.base import ProvedorEmpresas, empresa_vazia
from services.empresas import receita_cnpj
from unidecode import unidecode

PNCP_SEARCH_URL = os.getenv("PNCP_SEARCH_URL", "https://pncp.gov.br/api/search/").rstrip("/") + "/"
PNCP_ENABLED = os.getenv("PNCP_ENABLED", "1").strip() not in ("0", "false", "False", "")
TIMEOUT = 25.0
MAX_CNPJS_ENRIQUECER = 20   # limita enriquecimento (1 consulta MinhaReceita por CNPJ)


def _norm(s: str) -> str:
    s = unidecode(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _so_digitos(s) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _coletar_cnpjs(obj, achados: dict, termo_norm: str, nivel: int = 0):
    """
    Varre recursivamente a resposta do PNCP procurando pares
    (CNPJ de fornecedor, razão social). Como o schema do /search pode variar,
    procuramos por chaves conhecidas em qualquer profundidade.
    """
    if nivel > 6 or not isinstance(obj, (dict, list)):
        return
    if isinstance(obj, list):
        for it in obj:
            _coletar_cnpjs(it, achados, termo_norm, nivel + 1)
        return

    # dict: tenta extrair fornecedor deste nó
    ni = (obj.get("niFornecedor") or obj.get("ni_fornecedor")
          or obj.get("cnpjFornecedor") or obj.get("cnpj_fornecedor"))
    nome = (obj.get("nomeRazaoSocialFornecedor") or obj.get("nome_razao_social_fornecedor")
            or obj.get("razaoSocialFornecedor") or obj.get("nomeFornecedor") or "")
    cnpj = _so_digitos(ni)
    if len(cnpj) == 14 and cnpj not in achados:
        achados[cnpj] = (nome or "").strip()

    for v in obj.values():
        _coletar_cnpjs(v, achados, termo_norm, nivel + 1)


async def _buscar_pncp(client, termo: str, uf: str) -> dict:
    """
    Consulta o motor de busca do PNCP. Tenta combinações de parâmetro conhecidas
    (o /search aceita 'q' e tipos de documento). Retorna o JSON bruto ou {}.
    """
    tentativas = [
        {"q": termo, "tipos_documento": "edital", "ordenacao": "-data",
         "pagina": 1, "tam_pagina": 50, "status": "todos", "ufs": uf},
        {"q": termo, "tipos_documento": "edital", "pagina": 1, "ufs": uf},
        {"q": termo, "pagina": 1},
    ]
    for params in tentativas:
        params = {k: v for k, v in params.items() if v not in (None, "", [])}
        try:
            r = await client.get(PNCP_SEARCH_URL, params=params)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        try:
            data = r.json()
        except Exception:
            continue
        if data:
            return data
    return {}


class ProvedorPNCP(ProvedorEmpresas):
    nome = "pncp"

    def disponivel(self) -> bool:
        return PNCP_ENABLED

    async def buscar(
        self,
        *,
        nome_item: str,
        codigo_cat: str,
        tipo_cat: str,
        tipo_busca: str,
        cidade: str,
        estado: str,
        raio_km: int,
        lat: float | None = None,
        lng: float | None = None,
        max_empresas: int = 50,
        **kwargs,
    ) -> list[dict]:
        if not PNCP_ENABLED:
            return []

        uf = (estado or "").strip().upper()
        termo = " ".join((_norm(nome_item)).split()[:6])
        if not termo:
            return []

        cnpjs: dict[str, str] = {}
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            headers={"User-Agent": "OrcamentosPublicos/2.0", "Accept": "application/json"},
        ) as client:
            try:
                bruto = await _buscar_pncp(client, termo, uf)
            except Exception as e:
                print(f"[PNCP] busca falhou: {e}")
                return []
            _coletar_cnpjs(bruto, cnpjs, _norm(nome_item))

            if not cnpjs:
                return []

            # Enriquece os primeiros N CNPJs com endereço/telefone (MinhaReceita).
            alvos = list(cnpjs.items())[:MAX_CNPJS_ENRIQUECER]
            empresas: list[dict] = []
            for cnpj, nome_forn in alvos:
                emp = None
                try:
                    emp = await receita_cnpj.consultar_cnpj(cnpj, client=None)
                except Exception:
                    emp = None
                if emp is None:
                    # sem enriquecimento: ainda registra o que o PNCP deu
                    emp = empresa_vazia()
                    emp["cnpj"] = cnpj
                    emp["razao_social"] = nome_forn
                    emp["nome"] = nome_forn or cnpj
                emp["fonte"] = "pncp"
                emp["id"] = cnpj
                emp["ja_forneceu"] = True   # selo de alta confiança
                # filtra por UF quando possível (fornecedor pode ser de outra UF)
                if uf and emp.get("uf") and emp["uf"].upper() != uf:
                    # mantém, mas marca distância desconhecida; o agregador/raio decide
                    pass
                empresas.append(emp)
                await asyncio.sleep(0.1)

        return empresas[:max_empresas]

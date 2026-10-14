"""
Agregador de provedores de empresas.

Recebe a lista de fontes desejadas, executa cada provedor disponível em
paralelo, combina os resultados, deduplica (por CNPJ ou place_id/nome) e
devolve uma lista única no schema canônico.

Fontes aceitas (parâmetro `fontes`):
  - "receita" : base CNPJ da Receita Federal (padrão, não precisa de Maps)
  - "google"  : Google Maps/Places (só se a chave estiver configurada)
  - "ambas"   : combina as duas
"""
from __future__ import annotations

import asyncio

from services.empresas.receita_cnpj import ProvedorReceita
from services.empresas.google_maps import ProvedorGoogle

_PROVEDORES = {
    "receita": ProvedorReceita(),
    "google": ProvedorGoogle(),
}


def _resolver_fontes(fontes: str) -> list[str]:
    f = (fontes or "receita").strip().lower()
    if f in ("ambas", "todas", "both"):
        return ["receita", "google"]
    if f in _PROVEDORES:
        return [f]
    return ["receita"]  # padrão seguro


def _chave_dedup(emp: dict) -> str:
    cnpj = (emp.get("cnpj") or "").strip()
    if cnpj:
        return "cnpj:" + cnpj
    pid = (emp.get("id") or "").strip()
    if pid:
        return "id:" + pid
    return "nome:" + (emp.get("nome") or "").strip().lower()


async def buscar_empresas_agregado(
    *,
    nome_item: str,
    codigo_cat: str,
    tipo_cat: str,
    tipo_busca: str,
    cidade: str,
    estado: str,
    raio_km: int,
    fontes: str = "receita",
    descricao: str = "",
    max_empresas: int = 50,
) -> dict:
    """
    Orquestra a busca em uma ou mais fontes.

    Returns:
        {"empresas": [...], "fontes_usadas": [...], "avisos": [...]}
    """
    desejadas = _resolver_fontes(fontes)
    avisos: list[str] = []

    # Google precisa estar configurado; se não, avisa e remove da lista
    ativos: list[str] = []
    for nome in desejadas:
        prov = _PROVEDORES[nome]
        if prov.disponivel():
            ativos.append(nome)
        elif nome == "google":
            # Esperado quando não há chave; marcado como interno (frontend oculta).
            avisos.append("[interno] Google Maps não configurado.")

    if not ativos:
        # fallback garantido: Receita sempre está disponível
        ativos = ["receita"]

    tarefas = [
        _PROVEDORES[nome].buscar(
            nome_item=nome_item,
            codigo_cat=codigo_cat,
            tipo_cat=tipo_cat,
            tipo_busca=tipo_busca,
            cidade=cidade,
            estado=estado,
            raio_km=raio_km,
            max_empresas=max_empresas,
            descricao=descricao,
        )
        for nome in ativos
    ]
    resultados = await asyncio.gather(*tarefas, return_exceptions=True)

    combinadas: list[dict] = []
    vistos: set[str] = set()
    fontes_usadas: list[str] = []

    for nome, res in zip(ativos, resultados):
        if isinstance(res, Exception):
            # Mensagem amigável e visível ao usuário (não contém palavras que o
            # frontend filtra). Serve de diagnóstico quando a busca falha.
            avisos.append(f"⚠️ {res}")
            continue
        if res:
            fontes_usadas.append(nome)
        for emp in res:
            k = _chave_dedup(emp)
            if k in vistos:
                continue
            vistos.add(k)
            combinadas.append(emp)

    return {
        "empresas": combinadas[:max_empresas],
        "fontes_usadas": fontes_usadas,
        "avisos": avisos,
    }

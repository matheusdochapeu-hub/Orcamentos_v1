"""
Agregador de provedores de empresas.

Recebe a lista de fontes desejadas, executa cada provedor disponível em
paralelo, combina os resultados, deduplica (por CNPJ ou place_id/nome) e
devolve uma lista única no schema canônico.

Fontes aceitas (parâmetro `fontes`):
  - "pncp"    : quem JÁ FORNECEU o item ao poder público (alta confiança)
  - "receita" : base CNPJ da Receita Federal por CNAE (cobertura)
  - "google"  : Google Maps/Places (só se a chave estiver configurada)
  - "ambas"/"todas" : combina PNCP + Receita + Google (padrão do app)

Estratégia híbrida: o PNCP traz quem comprovadamente vende o produto (vai pro
topo), e a Receita/CNAE completa a lista. O Google entra se houver chave.
"""
from __future__ import annotations

import asyncio

from services.empresas.receita_cnpj import ProvedorReceita
from services.empresas.google_maps import ProvedorGoogle
from services.empresas.pncp import ProvedorPNCP
from services.empresas.osm import ProvedorOSM
from services.empresas.geoapify import ProvedorGeoapify

_PROVEDORES = {
    "pncp": ProvedorPNCP(),
    "receita": ProvedorReceita(),
    "osm": ProvedorOSM(),
    "geoapify": ProvedorGeoapify(),
    "google": ProvedorGoogle(),
}


def _resolver_fontes(fontes: str) -> list[str]:
    f = (fontes or "ambas").strip().lower()
    if f in ("ambas", "todas", "both", "hibrido", "híbrido"):
        return ["pncp", "receita", "osm", "geoapify", "google"]
    if f in _PROVEDORES:
        return [f]
    return ["pncp", "receita", "osm"]  # padrão seguro = híbrido


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
    maps_key: str = "",
    geoapify_key: str = "",
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
        if nome == "google":
            if prov.disponivel(maps_key):
                ativos.append(nome)
            else:
                avisos.append("[interno] Google Maps não configurado.")
        elif nome == "geoapify":
            if prov.disponivel(geoapify_key):
                ativos.append(nome)
        elif prov.disponivel():
            ativos.append(nome)

    if not ativos:
        # fallback garantido: Receita sempre está disponível
        ativos = ["receita"]

    # ORÇAMENTO DE TEMPO POR FONTE (segundos). Reduzido para evitar estouro
    # de timeout (504 Gateway Timeout) e atrasos ao usuário.
    _ORCAMENTO = {
        "receita": 8.0,
        "pncp": 8.0,
        "geoapify": 8.0,
        "osm": 8.0,
        "google": 8.0,
    }

    async def _rodar(nome: str):
        prov = _PROVEDORES[nome]
        try:
            return await asyncio.wait_for(
                prov.buscar(
                    nome_item=nome_item,
                    codigo_cat=codigo_cat,
                    tipo_cat=tipo_cat,
                    tipo_busca=tipo_busca,
                    cidade=cidade,
                    estado=estado,
                    raio_km=raio_km,
                    max_empresas=max_empresas,
                    descricao=descricao,
                    maps_key=maps_key,
                    geoapify_key=geoapify_key,
                ),
                timeout=_ORCAMENTO.get(nome, 8.0),
            )
        except asyncio.TimeoutError:
            return TimeoutError(f"fonte '{nome}' excedeu o tempo e foi ignorada")

    resultados = await asyncio.gather(*[_rodar(n) for n in ativos], return_exceptions=True)

    combinadas: list[dict] = []
    por_chave: dict[str, dict] = {}
    fontes_usadas: list[str] = []
    erros_relevantes: list[str] = []   # erros a mostrar SÓ se a busca vier vazia

    # Processa na ordem de 'ativos' (pncp primeiro → alta confiança tem prioridade)
    for nome, res in zip(ativos, resultados):
        if isinstance(res, Exception):
            # Complementos (PNCP/OSM/Geoapify/Google) nunca incomodam o usuário.
            if nome in ("pncp", "osm", "geoapify", "google"):
                avisos.append(f"[interno] {nome} indisponível: {res}")
            else:
                # Receita é a fonte principal, mas se as outras fontes trouxerem
                # resultados, a falha dela não precisa virar alarme vermelho.
                erros_relevantes.append(str(res))
            continue
        if res:
            fontes_usadas.append(nome)
        for emp in res:
            k = _chave_dedup(emp)
            if k in por_chave:
                # Já existe (provavelmente veio do PNCP): preserva o selo de
                # "já forneceu" e completa dados que faltarem (ex.: telefone).
                existente = por_chave[k]
                if emp.get("ja_forneceu"):
                    existente["ja_forneceu"] = True
                for campo in ("telefone", "email", "site", "endereco",
                              "cnae_principal", "cnae_descricao", "nome_fantasia"):
                    if not existente.get(campo) and emp.get(campo):
                        existente[campo] = emp[campo]
                continue
            por_chave[k] = emp
            combinadas.append(emp)

    # Empresas que já forneceram o item ao poder público vêm primeiro.
    combinadas.sort(key=lambda e: (0 if e.get("ja_forneceu") else 1))

    # Falha da Receita: só vira ⚠️ se NADA foi encontrado. Se as outras fontes
    # (PNCP/OSM/Google) trouxeram empresas, apenas registramos como interno.
    for msg in erros_relevantes:
        if combinadas:
            avisos.append(f"[interno] receita indisponível: {msg}")
        else:
            avisos.append(f"⚠️ {msg}")

    return {
        "empresas": combinadas[:max_empresas],
        "fontes_usadas": fontes_usadas,
        "avisos": avisos,
    }

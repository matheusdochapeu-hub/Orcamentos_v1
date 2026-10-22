"""
Provedor de empresas via Geoapify Places API.

Alternativa ao Google Places que é GRATUITA e NÃO EXIGE CARTÃO DE CRÉDITO:
  - Plano free: ~3.000 requisições/dia, uso comercial permitido.
  - Registro gratuito em geoapify.com (só e-mail; sem cartão).
  - Busca POIs por categoria + raio e traz nome, endereço, telefone, site.
  - Dados baseados em OpenStreetMap, mas com busca estruturada por categorias
    (mais prática que a Overpass crua).

Como o Google, a chave é do usuário (colada nas Configurações, salva só no
navegador) e vai no corpo da requisição de busca de empresas. Sem chave, este
provedor fica indisponível (a busca segue com as demais fontes).

Doc: https://apidocs.geoapify.com/docs/places/
"""
from __future__ import annotations

import os
import re

import httpx
from unidecode import unidecode

from services.empresas.base import ProvedorEmpresas, empresa_vazia
from services import geocode

PLACES_URL = "https://api.geoapify.com/v2/places"
TIMEOUT = 25.0

# Categoria RAIZ (sempre válida na taxonomia do Geoapify) — o recorte fino por
# produto é feito LOCALMENTE pelas palavras do item (como no OSM). Evitamos
# depender de subcategorias cujos nomes exatos variam e causam HTTP 400.
_CATEGORIA_MATERIAL = "commercial"
_CATEGORIA_SERVICO = "service"

# Subcategorias OPCIONAIS, usadas só se confirmadas válidas em runtime (há um
# fallback automático: se o Geoapify recusar com 400, refazemos com a raiz).
# Mantemos apenas um conjunto enxuto de candidatos plausíveis por palavra.
_SUBCATEGORIAS = {
    "supermercado": "commercial.supermarket", "alimento": "commercial.supermarket",
    "merenda": "commercial.supermarket", "genero": "commercial.supermarket",
    "medicamento": "healthcare.pharmacy", "farmaco": "healthcare.pharmacy",
}


def _categorias_para(nome_item: str, descricao: str, tipo_cat: str) -> tuple[str, str]:
    """
    Retorna (categoria_preferida, categoria_raiz).
    A preferida pode ser uma subcategoria plausível; a raiz é sempre válida e
    serve de fallback se a API recusar a preferida (HTTP 400).
    """
    raiz = (_CATEGORIA_MATERIAL if tipo_cat.upper() in ("CATMAT", "MATERIAL")
            else _CATEGORIA_SERVICO)
    texto = _norm(f"{nome_item} {descricao}")
    palavras = set(texto.split())
    for chave, cat in _SUBCATEGORIAS.items():
        if (" " in chave and chave in texto) or (chave in palavras):
            return cat, raiz
    return raiz, raiz

_STOP = {"de","da","do","dos","das","para","com","por","em","um","uma","e","a","o",
         "ltda","me","epp","comercio","servico","servicos"}


def _norm(s: str) -> str:
    s = unidecode(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _palavras(s: str) -> list[str]:
    return [p for p in _norm(s).split() if len(p) >= 4 and p not in _STOP]


def _chave_usuario() -> str:
    return (os.getenv("GEOAPIFY_API_KEY", "") or "").strip()


def _para_empresa(feat: dict) -> dict:
    props = feat.get("properties", {}) or {}
    emp = empresa_vazia()
    emp["fonte"] = "geoapify"
    emp["id"] = "geoapify:" + str(props.get("place_id", "") or props.get("osm_id", ""))
    emp["nome"] = props.get("name", "") or ""
    emp["razao_social"] = emp["nome"]
    emp["endereco"] = props.get("formatted", "") or props.get("address_line2", "")
    emp["municipio"] = props.get("city", "")
    emp["uf"] = props.get("state_code", "") or props.get("state", "")
    emp["lat"] = props.get("lat")
    emp["lng"] = props.get("lon")

    contact = props.get("contact", {}) or {}
    datasource_raw = (props.get("datasource", {}) or {}).get("raw", {}) or {}
    emp["telefone"] = (contact.get("phone") or datasource_raw.get("phone")
                       or datasource_raw.get("contact:phone") or "")
    emp["email"] = contact.get("email") or datasource_raw.get("email") or ""
    emp["site"] = (props.get("website") or contact.get("website")
                   or datasource_raw.get("website") or "")
    emp["horario"] = props.get("opening_hours") or datasource_raw.get("opening_hours") or ""
    cats = props.get("categories") or []
    if cats:
        emp["cnae_descricao"] = ", ".join(cats[:2])
    return emp


class ProvedorGeoapify(ProvedorEmpresas):
    nome = "geoapify"

    def disponivel(self, geoapify_key: str = "") -> bool:
        return bool(geoapify_key or _chave_usuario())

    async def buscar(self, *, nome_item, codigo_cat, tipo_cat, tipo_busca,
                     cidade, estado, raio_km, lat=None, lng=None,
                     max_empresas=50, geoapify_key: str = "", **kwargs) -> list[dict]:
        chave = geoapify_key or kwargs.get("geoapify_key", "") or _chave_usuario()
        if not chave:
            return []
        palavras_item = _palavras(f"{nome_item} {kwargs.get('descricao','')}")

        if lat is None or lng is None:
            centro = await geocode.geocodificar(
                f"{cidade}, {estado}, Brasil" if estado else f"{cidade}, Brasil"
            )
            if not centro:
                return []
            lat, lng = centro["lat"], centro["lng"]

        raio_m = min(max(raio_km, 1), 100) * 1000
        preferida, raiz = _categorias_para(nome_item, kwargs.get("descricao", ""), tipo_cat)

        # A Places API do Geoapify filtra por ESPAÇO (categoria + área), não por
        # texto livre. Pedimos os POIs da categoria no raio e filtramos pelas
        # palavras do item LOCALMENTE (como no OSM). O filtro espacial 'circle'
        # usa a ordem lon,lat.
        def _montar_params(cat: str) -> dict:
            return {
                "categories": cat,
                "filter": f"circle:{lng},{lat},{raio_m}",
                "bias": f"proximity:{lng},{lat}",
                "limit": 200,
                "apiKey": chave,
                "lang": "pt",
            }

        feats = []
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT, headers={"Accept": "application/json"}
            ) as client:
                r = await client.get(PLACES_URL, params=_montar_params(preferida))
                # AUTO-CURA: se a categoria preferida não for suportada (HTTP 400),
                # refaz com a categoria RAIZ (sempre válida).
                if r.status_code == 400 and preferida != raiz:
                    print(f"[Geoapify] categoria '{preferida}' recusada; usando '{raiz}'")
                    r = await client.get(PLACES_URL, params=_montar_params(raiz))
                if r.status_code != 200:
                    print(f"[Geoapify] HTTP {r.status_code}")
                    return []
                feats = r.json().get("features", []) or []
        except Exception as e:
            print(f"[Geoapify] {e}")
            return []

        empresas = []
        for feat in feats:
            emp = _para_empresa(feat)
            if not emp["nome"]:
                continue
            # Recorte fino pelo produto: exige casar ao menos 1 palavra do item
            # no nome/categorias (a categoria raiz é ampla).
            if palavras_item:
                alvo = _norm(f"{emp['nome']} {emp.get('cnae_descricao','')}")
                if not any(p in alvo for p in palavras_item):
                    continue
            if emp.get("lat") and emp.get("lng"):
                d = geocode.distancia_km(lat, lng, emp["lat"], emp["lng"])
                emp["distancia_km"] = round(d, 1)
            empresas.append(emp)

        empresas.sort(key=lambda e: (e.get("distancia_km") is None, e.get("distancia_km") or 0))
        return empresas[:max_empresas]

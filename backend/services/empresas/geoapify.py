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

# Mapa simples de intenção → categorias Geoapify (taxonomia própria do Geoapify).
# commercial.* cobre lojas/varejo; service.* cobre serviços.
_CATEGORIAS_MATERIAL = "commercial"
_CATEGORIAS_SERVICO = "service,commercial.business_to_business"

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

    def disponivel(self) -> bool:
        return bool(_chave_usuario())

    async def buscar(self, *, nome_item, codigo_cat, tipo_cat, tipo_busca,
                     cidade, estado, raio_km, lat=None, lng=None,
                     max_empresas=50, **kwargs) -> list[dict]:
        chave = _chave_usuario()
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
        categorias = (_CATEGORIAS_MATERIAL
                      if tipo_cat.upper() in ("CATMAT", "MATERIAL")
                      else _CATEGORIAS_SERVICO)

        # A Places API do Geoapify filtra por ESPAÇO (categoria + área), não por
        # texto livre. Então pedimos os POIs da categoria no raio e filtramos
        # pelas palavras do item LOCALMENTE (como fazemos no OSM). Pedimos um
        # limite maior porque o recorte fino é feito aqui.
        params = {
            "categories": categorias,
            "filter": f"circle:{lng},{lat},{raio_m}",
            "bias": f"proximity:{lng},{lat}",
            "limit": 200,
            "apiKey": chave,
            "lang": "pt",
        }

        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT, headers={"Accept": "application/json"}
            ) as client:
                r = await client.get(PLACES_URL, params=params)
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
            # casa com o item pelas palavras (nome + categorias)
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

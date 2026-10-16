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

# Categorias amplas (fallback). As categorias-raiz do Geoapify são válidas e
# incluem todas as subcategorias abaixo delas.
_CATEGORIAS_MATERIAL = "commercial"
_CATEGORIAS_SERVICO = "service,commercial.business_to_business"

# Mapa palavra-chave do item -> subcategorias ESPECÍFICAS do Geoapify.
# Quando o termo buscado casa aqui, pedimos a subcategoria certa (resultado mais
# preciso) em vez da categoria ampla. Taxonomia: docs Geoapify (categories).
_SUBCATEGORIAS = {
    # material / produtos
    "movel": "commercial.furniture", "mobiliario": "commercial.furniture",
    "cadeira": "commercial.furniture", "mesa": "commercial.furniture",
    "estofado": "commercial.furniture", "sofa": "commercial.furniture",
    "construcao": "commercial.hardware", "ferramenta": "commercial.hardware",
    "ferragem": "commercial.hardware", "material eletrico": "commercial.hardware",
    "hidraulico": "commercial.hardware", "tinta": "commercial.hardware",
    "eletronico": "commercial.electronics", "computador": "commercial.electronics",
    "notebook": "commercial.electronics", "informatica": "commercial.electronics",
    "celular": "commercial.electronics", "impressora": "commercial.electronics",
    "eletrodomestico": "commercial.electronics",
    "papel": "commercial.stationery", "papelaria": "commercial.stationery",
    "escritorio": "commercial.stationery", "caneta": "commercial.stationery",
    "livro": "commercial.books", "didatico": "commercial.books",
    "alimento": "commercial.supermarket", "merenda": "commercial.supermarket",
    "genero": "commercial.supermarket", "cesta": "commercial.supermarket",
    "medicamento": "commercial.health_and_beauty.pharmacy",
    "farmaco": "commercial.health_and_beauty.pharmacy",
    "hospitalar": "commercial.health_and_beauty.medical_supply",
    "limpeza": "commercial.chemist", "higiene": "commercial.chemist",
    "uniforme": "commercial.clothing", "vestuario": "commercial.clothing",
    "fardamento": "commercial.clothing", "calcado": "commercial.shoes",
    "pneu": "commercial.vehicle", "veiculo": "commercial.vehicle",
    "autopeca": "commercial.vehicle", "peca": "commercial.vehicle",
    "combustivel": "commercial.gas", "gas": "commercial.gas",
    "jardinagem": "commercial.garden", "agricola": "commercial.agrarian",
    "racao": "commercial.pet",
    # serviços
    "manutencao": "service.vehicle", "pintura": "service", "obra": "service",
    "grafica": "commercial.printing", "impressao": "commercial.printing",
    "lavanderia": "service.cleaning", "dedetizacao": "service.cleaning",
}


def _categorias_para(nome_item: str, descricao: str, tipo_cat: str) -> str:
    """Escolhe subcategorias específicas quando o item casa; senão, as amplas."""
    texto = _norm(f"{nome_item} {descricao}")
    palavras = set(texto.split())
    especificas: list[str] = []
    for chave, cat in _SUBCATEGORIAS.items():
        if " " in chave:
            if chave in texto and cat not in especificas:
                especificas.append(cat)
        elif chave in palavras and cat not in especificas:
            especificas.append(cat)
    if especificas:
        return ",".join(especificas[:4])
    return (_CATEGORIAS_MATERIAL
            if tipo_cat.upper() in ("CATMAT", "MATERIAL")
            else _CATEGORIAS_SERVICO)

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
        categorias = _categorias_para(nome_item, kwargs.get("descricao", ""), tipo_cat)
        # Se usamos subcategorias específicas, elas já recortam bem; o filtro
        # textual local vira só um reforço opcional (não descartamos tudo).
        usou_especifica = categorias not in (_CATEGORIAS_MATERIAL, _CATEGORIAS_SERVICO)

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
            # Com subcategoria específica, confiamos no recorte da API e mantemos
            # todos. Com categoria ampla, exigimos casar as palavras do item.
            if palavras_item and not usou_especifica:
                alvo = _norm(f"{emp['nome']} {emp.get('cnae_descricao','')}")
                if not any(p in alvo for p in palavras_item):
                    continue
            if emp.get("lat") and emp.get("lng"):
                d = geocode.distancia_km(lat, lng, emp["lat"], emp["lng"])
                emp["distancia_km"] = round(d, 1)
            empresas.append(emp)

        empresas.sort(key=lambda e: (e.get("distancia_km") is None, e.get("distancia_km") or 0))
        return empresas[:max_empresas]

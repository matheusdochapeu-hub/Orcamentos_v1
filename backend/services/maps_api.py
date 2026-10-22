"""
Integração com Google Maps APIs:
  - Geocoding: cidade → coordenadas
  - Places Nearby: busca de empresas por raio geográfico
  - Place Details: dados completos da empresa
"""
import httpx
import os
import json
import hashlib
from datetime import datetime, timedelta
from typing import Optional

GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACES_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
PLACES_TEXT_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
TIMEOUT = 20.0


def _get_api_key(api_key: str = "") -> str:
    key = api_key or os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not key or key == "SUA_CHAVE_AQUI":
        raise ValueError(
            "Google Maps API Key não configurada. "
            "Edite o arquivo .env e adicione sua chave em GOOGLE_MAPS_API_KEY."
        )
    return key


def _cache_key(params: dict) -> str:
    raw = json.dumps(params, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


async def geocodificar_cidade(cidade: str, estado: str = "", api_key: str = "") -> dict:
    """
    Converte nome de cidade em coordenadas lat/lng.
    Retorna: {"lat": float, "lng": float, "endereco_formatado": str}
    """
    key = _get_api_key(api_key)
    endereco = f"{cidade}, {estado}, Brasil" if estado else f"{cidade}, Brasil"

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(
            GEOCODING_URL,
            params={"address": endereco, "key": key, "language": "pt-BR"},
        )
        data = r.json()

    if data.get("status") != "OK" or not data.get("results"):
        raise ValueError(f"Cidade '{cidade}' não encontrada. Verifique o nome e tente novamente.")

    result = data["results"][0]
    loc = result["geometry"]["location"]
    return {
        "lat": loc["lat"],
        "lng": loc["lng"],
        "endereco_formatado": result.get("formatted_address", endereco),
    }


import math as _math

def _distancia_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distância real entre dois pontos via Haversine."""
    R    = 6371.0
    dlat = _math.radians(lat2 - lat1)
    dlng = _math.radians(lng2 - lng1)
    a    = (_math.sin(dlat/2)**2
            + _math.cos(_math.radians(lat1)) * _math.cos(_math.radians(lat2))
            * _math.sin(dlng/2)**2)
    return R * 2 * _math.atan2(_math.sqrt(a), _math.sqrt(1 - a))


def _construir_query_busca(
    nome_item: str,
    codigo: str,
    tipo: str,
    tipo_busca: str,
) -> list[str]:
    """Constrói queries para buscar no Google Places."""
    stop = {"de","da","do","dos","das","para","com","por","em","um","uma","e","a","o"}
    palavras = [p for p in nome_item.split() if len(p) > 2 and p.lower() not in stop][:4]
    termo_base = " ".join(palavras)

    if tipo == "CATMAT":
        prefixos = ["fornecedor", "distribuidora", "loja"]
    else:
        prefixos = ["empresa", "prestadora de serviço"]

    queries = [f"{prefixo} {termo_base}" for prefixo in prefixos[:2]]
    if tipo_busca == "similaridade":
        queries.append(termo_base)
    return queries


async def buscar_empresas_nearby(
    lat: float,
    lng: float,
    raio_metros: int,
    keyword: str,
    api_key: str = "",
) -> list[dict]:
    """
    Busca empresas no Google Places e filtra pelo raio real (Haversine).
    O Google Places às vezes retorna resultados além do raio — aplicamos
    nosso próprio filtro de distância para garantir a precisão.
    """
    key       = _get_api_key(api_key)
    empresas  = []
    next_page_token = None
    paginas   = 0
    raio_api  = min(raio_metros, 50000)

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        while paginas < 2:
            params = {
                "location": f"{lat},{lng}",
                "radius":   raio_api,
                "keyword":  keyword,
                "key":      key,
                "language": "pt-BR",
            }
            if next_page_token:
                params = {"pagetoken": next_page_token, "key": key}

            r    = await client.get(PLACES_NEARBY_URL, params=params)
            data = r.json()

            if data.get("status") not in ("OK", "ZERO_RESULTS"):
                break

            for place in data.get("results", []):
                emp = _extrair_dados_place(place)
                # Filtra pelo raio real usando Haversine
                if emp.get("lat") and emp.get("lng"):
                    dist = _distancia_km(lat, lng, emp["lat"], emp["lng"])
                    if dist * 1000 <= raio_metros:
                        emp["distancia_km"] = round(dist, 1)
                        empresas.append(emp)
                else:
                    empresas.append(emp)

            next_page_token = data.get("next_page_token")
            paginas += 1
            if not next_page_token:
                break

            import asyncio
            await asyncio.sleep(2)

    return empresas


async def buscar_empresas_text(
    lat: float,
    lng: float,
    raio_metros: int,
    query: str,
    api_key: str = "",
) -> list[dict]:
    """
    Busca empresas via Text Search (mais flexível que Nearby para raios > 50km).
    """
    key = _get_api_key(api_key)
    empresas = []

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        params = {
            "query": query,
            "location": f"{lat},{lng}",
            "radius": min(raio_metros, 50000),
            "key": key,
            "language": "pt-BR",
        }
        r = await client.get(PLACES_TEXT_URL, params=params)
        data = r.json()

        for place in data.get("results", []):
            empresas.append(_extrair_dados_place(place))

    return empresas


async def obter_detalhes_empresa(place_id: str, api_key: str = "") -> dict:
    """
    Obtém dados detalhados de uma empresa via Place Details.
    Inclui telefone, site, horários, avaliações.
    """
    key = _get_api_key(api_key)
    campos = "name,formatted_address,formatted_phone_number,website,rating,reviews,types,url,opening_hours,editorial_summary"

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(
            PLACE_DETAILS_URL,
            params={
                "place_id": place_id,
                "fields": campos,
                "key": key,
                "language": "pt-BR",
            },
        )
        data = r.json()

    result = data.get("result", {})
    return {
        "telefone": result.get("formatted_phone_number", ""),
        "site": result.get("website", ""),
        "avaliacao": result.get("rating", 0),
        "tipos": result.get("types", []),
        "reviews": [
            r.get("text", "") for r in result.get("reviews", [])[:5]
        ],
        "descricao": result.get("editorial_summary", {}).get("overview", ""),
        "google_url": result.get("url", ""),
    }


def _extrair_dados_place(place: dict) -> dict:
    """Extrai e normaliza dados brutos do Google Places."""
    loc = place.get("geometry", {}).get("location", {})
    return {
        "place_id": place.get("place_id", ""),
        "nome": place.get("name", ""),
        "endereco": place.get("vicinity", place.get("formatted_address", "")),
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "avaliacao_google": place.get("rating", 0),
        "total_avaliacoes": place.get("user_ratings_total", 0),
        "tipos": place.get("types", []),
        "aberto_agora": place.get("opening_hours", {}).get("open_now"),
        "foto_ref": (place.get("photos") or [{}])[0].get("photo_reference", ""),
        # Detalhes extras (preenchidos depois via Place Details)
        "telefone": "",
        "whatsapp": "",
        "email": "",
        "site": "",
        "reviews": [],
        "relevancia": 0,
    }


async def buscar_todas_empresas(
    lat: float,
    lng: float,
    raio_km: int,
    nome_item: str,
    codigo_cat: str,
    tipo_cat: str,
    tipo_busca: str,
    max_empresas: int = 50,
    api_key: str = "",
) -> list[dict]:
    """
    Orquestra a busca completa de empresas combinando múltiplas queries.
    Para raios > 50km, divide em sub-buscas concêntricas.
    """
    import asyncio

    raio_metros = raio_km * 1000
    queries = _construir_query_busca(nome_item, codigo_cat, tipo_cat, tipo_busca)

    # Para raios grandes (> 50km), Google Places não suporta, então usa Text Search
    if raio_km > 50:
        busca_fn = buscar_empresas_text
    else:
        busca_fn = buscar_empresas_nearby

    tarefas = [busca_fn(lat, lng, raio_metros, q, api_key=api_key) for q in queries]
    resultados = await asyncio.gather(*tarefas, return_exceptions=True)

    # Combinar e deduplicar por place_id
    vistos = set()
    todas = []
    for lista in resultados:
        if isinstance(lista, list):
            for emp in lista:
                pid = emp.get("place_id") or emp.get("nome")
                if pid and pid not in vistos:
                    vistos.add(pid)
                    todas.append(emp)

    # Obter detalhes das primeiras max_empresas (limitar chamadas à API)
    todas = todas[:max_empresas]

    # Busca detalhes em paralelo (lotes de 5 para não sobrecarregar)
    lote_size = 5
    for i in range(0, len(todas), lote_size):
        lote = todas[i : i + lote_size]
        detalhes = await asyncio.gather(
            *[
                obter_detalhes_empresa(e["place_id"], api_key=api_key)
                for e in lote
                if e.get("place_id")
            ],
            return_exceptions=True,
        )
        for j, det in enumerate(detalhes):
            if isinstance(det, dict):
                todas[i + j].update(det)

    return todas

"""
Provedor de empresas via Google Maps/Places (OPCIONAL).

Mantido para quem quiser usar o Google como complemento. Agora é apenas UM dos
provedores — o sistema funciona sem ele usando o provedor da Receita.

Requer GOOGLE_MAPS_API_KEY (paga, com cota gratuita mensal). Reaproveita as
funções de baixo nível em services/maps_api.py.
"""
from __future__ import annotations

import os

from services.empresas.base import ProvedorEmpresas, empresa_vazia
from services import maps_api


def _para_schema(bruto: dict) -> dict:
    """Converte o dict do maps_api para o schema canônico."""
    emp = empresa_vazia()
    emp["fonte"] = "google"
    emp["id"] = bruto.get("place_id", "")
    emp["nome"] = bruto.get("nome", "")
    emp["razao_social"] = bruto.get("nome", "")
    emp["endereco"] = bruto.get("endereco", "")
    emp["lat"] = bruto.get("lat")
    emp["lng"] = bruto.get("lng")
    emp["distancia_km"] = bruto.get("distancia_km")
    emp["telefone"] = bruto.get("telefone", "")
    emp["site"] = bruto.get("site", "")
    emp["email"] = bruto.get("email", "")
    emp["tipos"] = bruto.get("tipos", [])
    emp["avaliacao_google"] = bruto.get("avaliacao_google", 0) or bruto.get("avaliacao", 0)
    emp["total_avaliacoes"] = bruto.get("total_avaliacoes", 0)
    emp["reviews"] = bruto.get("reviews", [])
    emp["descricao"] = bruto.get("descricao", "")
    return emp


class ProvedorGoogle(ProvedorEmpresas):
    nome = "google"

    def disponivel(self) -> bool:
        chave = os.getenv("GOOGLE_MAPS_API_KEY", "")
        return bool(chave and chave != "SUA_CHAVE_AQUI")

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
        if lat is None or lng is None:
            coords = await maps_api.geocodificar_cidade(cidade, estado)
            lat, lng = coords["lat"], coords["lng"]

        brutos = await maps_api.buscar_todas_empresas(
            lat=lat,
            lng=lng,
            raio_km=raio_km,
            nome_item=nome_item,
            codigo_cat=codigo_cat,
            tipo_cat=tipo_cat,
            tipo_busca=tipo_busca,
            max_empresas=max_empresas,
        )
        return [_para_schema(b) for b in brutos]

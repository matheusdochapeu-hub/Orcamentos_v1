"""
Rotas da API para busca de empresas.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from services.maps_api import geocodificar_cidade, buscar_todas_empresas
from services.relevancia import classificar_empresas

router = APIRouter(prefix="/api/empresas", tags=["Empresas"])


class BuscaEmpresasRequest(BaseModel):
    # Item selecionado
    nome_item: str
    codigo_cat: str
    tipo_cat: str        # "CATMAT" | "CATSER"

    # Localização
    cidade: str
    estado: Optional[str] = ""
    raio_km: int         # 1 a 200

    # Tipo de busca
    tipo_busca: str      # "exata" | "similaridade"

    # Google Maps Key do usuário (opcional — se não fornecida, usa .env)
    maps_key: Optional[str] = ""


@router.post("/buscar")
async def buscar_empresas(req: BuscaEmpresasRequest):
    """
    Busca empresas na região informada e retorna lista com percentual de relevância.
    """
    if req.raio_km < 1 or req.raio_km > 200:
        raise HTTPException(400, "Raio deve estar entre 1 e 200 km")

    if not req.cidade.strip():
        raise HTTPException(400, "Cidade não pode ser vazia")

    # Sobrescreve chave de API se o usuário forneceu a sua
    import os
    if req.maps_key and req.maps_key.strip():
        os.environ["GOOGLE_MAPS_API_KEY"] = req.maps_key.strip()

    # 1. Geocodifica a cidade
    try:
        coords = await geocodificar_cidade(req.cidade.strip(), req.estado or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erro no serviço de geocodificação: {str(e)}")

    # 2. Busca empresas
    try:
        empresas = await buscar_todas_empresas(
            lat=coords["lat"],
            lng=coords["lng"],
            raio_km=req.raio_km,
            nome_item=req.nome_item,
            codigo_cat=req.codigo_cat,
            tipo_cat=req.tipo_cat,
            tipo_busca=req.tipo_busca,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erro ao buscar empresas: {str(e)}")

    # 3. Calcula relevância e ordena
    empresas = classificar_empresas(
        empresas=empresas,
        nome_item=req.nome_item,
        tipo_cat=req.tipo_cat,
        tipo_busca=req.tipo_busca,
    )

    return {
        "coords": coords,
        "total": len(empresas),
        "empresas": empresas,
    }

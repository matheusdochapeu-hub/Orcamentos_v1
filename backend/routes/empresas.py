"""
Rotas da API para busca de empresas.

A busca agora é multi-fonte: por padrão usa a BASE CNPJ DA RECEITA FEDERAL
(CNAE + município + UF), que NÃO depende do Google Maps. O Google é opcional e
pode ser combinado via o parâmetro `fontes`.
"""
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from services.empresas.agregador import buscar_empresas_agregado
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

    # Fonte dos dados: "ambas" (padrão) combina todas as fontes disponíveis
    fontes: Optional[str] = "ambas"

    # Chaves opcionais do usuário (salvas só no navegador)
    maps_key: Optional[str] = ""       # Google Maps (exige cartão no Google)
    geoapify_key: Optional[str] = ""   # Geoapify (grátis, sem cartão)


@router.post("/buscar")
async def buscar_empresas(req: BuscaEmpresasRequest):
    """
    Busca empresas na região informada e retorna lista com percentual de
    relevância. Funciona SEM Google Maps (fonte padrão: Receita Federal).
    """
    if req.raio_km < 1 or req.raio_km > 200:
        raise HTTPException(400, "Raio deve estar entre 1 e 200 km")

    if not req.cidade.strip():
        raise HTTPException(400, "Cidade não pode ser vazia")

    # Chave do Google: continua opcional e vem do usuário (exige cartão no Google).
    if req.maps_key and req.maps_key.strip():
        os.environ["GOOGLE_MAPS_API_KEY"] = req.maps_key.strip()

    # Chave do Geoapify: é FIXA no servidor (variável de ambiente GEOAPIFY_API_KEY),
    # usada como motor de busca para todos os usuários — eles não a veem nem a
    # informam. Só aceitamos uma chave vinda do cliente se o servidor NÃO tiver
    # uma configurada (ex.: ambiente de desenvolvimento local).
    if not (os.getenv("GEOAPIFY_API_KEY") or "").strip():
        if req.geoapify_key and req.geoapify_key.strip():
            os.environ["GEOAPIFY_API_KEY"] = req.geoapify_key.strip()

    try:
        resultado = await buscar_empresas_agregado(
            nome_item=req.nome_item,
            codigo_cat=req.codigo_cat,
            tipo_cat=req.tipo_cat,
            tipo_busca=req.tipo_busca,
            cidade=req.cidade.strip(),
            estado=(req.estado or "").strip(),
            raio_km=req.raio_km,
            fontes=req.fontes or "receita",
            max_empresas=50,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erro ao buscar empresas: {str(e)}")

    # Enriquecimento via OpenStreetMap: completa telefone/site/horário das
    # empresas (Receita/PNCP) cruzando por nome + proximidade. Grátis, sem chave;
    # degrada em silêncio.
    try:
        from services.empresas.osm import enriquecer as enriquecer_osm, OSM_ENABLED
        if OSM_ENABLED:
            resultado["empresas"] = await enriquecer_osm(
                resultado["empresas"],
                cidade=req.cidade.strip(),
                estado=(req.estado or "").strip(),
                raio_km=req.raio_km,
            )
    except Exception as e:
        print(f"[OSM enriquecer] ignorado: {e}")

    empresas = classificar_empresas(
        empresas=resultado["empresas"],
        nome_item=req.nome_item,
        tipo_cat=req.tipo_cat,
        tipo_busca=req.tipo_busca,
    )

    # Cruzamento opcional com CEIS/CNEP (empresas sancionadas) — só roda se a
    # chave do Portal da Transparência estiver configurada; degrada em silêncio.
    try:
        from services.sancoes import marcar_sancoes, habilitado
        if habilitado():
            empresas = await marcar_sancoes(empresas)
    except Exception as e:
        print(f"[Sancoes] ignorado: {e}")

    return {
        "total": len(empresas),
        "empresas": empresas,
        "fontes_usadas": resultado["fontes_usadas"],
        "avisos": resultado["avisos"],
    }

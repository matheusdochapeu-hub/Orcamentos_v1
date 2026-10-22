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

    # Estado (UF) é necessário para resolver o município (código IBGE) e filtrar
    # as empresas na região certa. Sem ele a busca falharia só lá no fim, sem
    # resultado — então recusamos cedo, com mensagem clara (o frontend também
    # bloqueia o botão, isto protege chamadas diretas à API / embed).
    if not (req.estado or "").strip():
        raise HTTPException(400, "Selecione o estado (UF) antes de buscar")

    # Chaves de API tratadas de forma contextual e isolada (sem poluir os.environ):
    chave_maps = req.maps_key.strip() if req.maps_key else os.getenv("GOOGLE_MAPS_API_KEY", "")
    chave_geo = os.getenv("GEOAPIFY_API_KEY", "")
    if not chave_geo and req.geoapify_key:
        chave_geo = req.geoapify_key.strip()

    try:
        resultado = await buscar_empresas_agregado(
            nome_item=req.nome_item,
            codigo_cat=req.codigo_cat,
            tipo_cat=req.tipo_cat,
            tipo_busca=req.tipo_busca,
            cidade=req.cidade.strip(),
            estado=(req.estado or "").strip(),
            raio_km=req.raio_km,
            fontes=req.fontes or "ambas",
            max_empresas=50,
            maps_key=chave_maps,
            geoapify_key=chave_geo,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erro ao buscar empresas: {str(e)}")

    # Enriquecimento via OpenStreetMap: completa telefone/site/horário das
    # empresas (Receita/PNCP) cruzando por nome + proximidade. Grátis, sem chave;
    # degrada em silêncio.
    import asyncio
    try:
        from services.empresas.osm import enriquecer as enriquecer_osm, OSM_ENABLED
        if OSM_ENABLED:
            # Limite próprio reduzido para não atrasar a resposta ao usuário.
            resultado["empresas"] = await asyncio.wait_for(
                enriquecer_osm(
                    resultado["empresas"],
                    cidade=req.cidade.strip(),
                    estado=(req.estado or "").strip(),
                    raio_km=req.raio_km,
                ),
                timeout=5.0,
            )
    except asyncio.TimeoutError:
        print("[OSM enriquecer] tempo excedido — seguindo sem enriquecimento")
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
            empresas = await asyncio.wait_for(marcar_sancoes(empresas), timeout=4.0)
    except asyncio.TimeoutError:
        print("[Sancoes] tempo excedido — seguindo sem verificação de sanções")
    except Exception as e:
        print(f"[Sancoes] ignorado: {e}")

    return {
        "total": len(empresas),
        "empresas": empresas,
        "fontes_usadas": resultado["fontes_usadas"],
        "avisos": resultado["avisos"],
    }

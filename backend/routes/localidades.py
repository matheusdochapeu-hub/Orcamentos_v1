"""
Rotas de localidades (IBGE): lista de municípios por UF.

Usada pelo frontend para preencher o autocomplete de cidade depois que o
usuário escolhe o estado. Dados vêm da API pública de Localidades do IBGE
(via services.ibge), com cache local — então a lista fica instantânea após a
primeira consulta de cada UF.
"""
from fastapi import APIRouter, HTTPException

from services.ibge import listar_municipios

router = APIRouter(prefix="/api/localidades", tags=["Localidades"])

_UFS = {
    "AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB",
    "PR","PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO",
}


@router.get("/municipios/{uf}")
async def municipios(uf: str):
    """Retorna {uf, municipios: [nomes...]} para a UF informada."""
    uf = (uf or "").strip().upper()
    if uf not in _UFS:
        raise HTTPException(400, "UF inválida")
    nomes = await listar_municipios(uf)
    return {"uf": uf, "total": len(nomes), "municipios": nomes}

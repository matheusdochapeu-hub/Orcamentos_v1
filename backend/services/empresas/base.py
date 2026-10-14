"""
Contrato comum dos provedores de busca de empresas.

Toda empresa devolvida por qualquer provedor DEVE seguir o schema de
`empresa_vazia()`. Isso permite que o frontend, o cálculo de relevância e a
exportação funcionem igual, independentemente da fonte dos dados.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


def empresa_vazia() -> dict:
    """Schema canônico de uma empresa. Todo provedor normaliza para este formato."""
    return {
        # Identificação
        "id": "",                 # identificador único na fonte (place_id, cnpj…)
        "fonte": "",              # "receita" | "google"
        "nome": "",               # nome de exibição (fantasia; senão razão social)
        "nome_fantasia": "",      # nome fantasia puro (quando houver)
        "razao_social": "",
        "cnpj": "",
        # Localização
        "endereco": "",
        "municipio": "",
        "uf": "",
        "cep": "",
        "lat": None,
        "lng": None,
        "distancia_km": None,
        # Contato
        "telefone": "",
        "whatsapp": "",
        "email": "",
        "site": "",
        # Atividade / classificação
        "cnae_principal": "",
        "cnae_descricao": "",
        "tipos": [],              # tipos Google Places (quando aplicável)
        "situacao": "",           # ATIVA / etc. (Receita)
        # Qualidade / reputação (quando houver)
        "avaliacao_google": 0,
        "total_avaliacoes": 0,
        "reviews": [],
        "descricao": "",
        # Score calculado depois
        "relevancia": 0,
    }


class ProvedorEmpresas(ABC):
    """Interface que todo provedor de empresas implementa."""

    nome: str = "base"

    @abstractmethod
    async def buscar(
        self,
        *,
        nome_item: str,
        codigo_cat: str,
        tipo_cat: str,          # "CATMAT" | "CATSER"
        tipo_busca: str,        # "exata" | "similaridade"
        cidade: str,
        estado: str,
        raio_km: int,
        lat: float | None = None,
        lng: float | None = None,
        max_empresas: int = 50,
        **kwargs,
    ) -> list[dict]:
        """Retorna uma lista de empresas no schema de `empresa_vazia()`."""
        raise NotImplementedError

    def disponivel(self) -> bool:
        """Indica se o provedor está pronto para uso (ex.: chave configurada)."""
        return True

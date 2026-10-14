"""
Camada de provedores de busca de empresas fornecedoras.

Cada provedor implementa a mesma interface (ver base.py) e devolve uma lista de
dicionários de empresa normalizados. O agregador (agregador.py) combina os
resultados de um ou mais provedores, deduplica e devolve um resultado único.

Provedores disponíveis:
  - receita   → base CNPJ da Receita Federal (CNAE + município + UF). GRÁTIS, não
                depende do Google Maps. É o provedor padrão/recomendado.
  - google    → Google Maps/Places. Requer chave paga. Mantido como opcional.
"""

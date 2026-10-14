"""
Mapeamento de termos (CATMAT/CATSER) para CNAEs (Classificação Nacional de
Atividades Econômicas) usados para encontrar empresas fornecedoras na base da
Receita Federal — SEM depender do Google Maps.

Como funciona:
  - O usuário escolhe um item CATMAT/CATSER (ex.: "cadeira de escritório").
  - Extraímos palavras-chave do nome/descrição do item.
  - Cada palavra-chave aponta para um ou mais CNAEs onde empresas que vendem
    aquele tipo de produto/serviço costumam estar classificadas.
  - A busca na Receita filtra empresas ATIVAS por CNAE + município + UF.

Observações:
  - Este mapa é intencionalmente heurístico e EXPANSÍVEL. Não precisa ser
    exaustivo: ele cobre as famílias de itens mais comuns em compra pública.
  - Quando nenhuma palavra casa, devolvemos uma lista de CNAEs genéricos de
    comércio varejista/atacadista (materiais) ou de serviços, para não zerar.
  - CNAEs são strings de 7 dígitos (sem pontuação), formato da base da Receita.
"""
from __future__ import annotations

import re
from unidecode import unidecode


# ─── Famílias de produtos (CATMAT) ─────────────────────────────────────────────
# chave = palavra/raiz (sem acento, minúscula) → lista de CNAEs relevantes
_MAPA_MATERIAL: dict[str, list[str]] = {
    # Mobiliário
    "cadeira":      ["4754701", "3101200", "4649404"],   # móveis comércio / fabricação
    "mesa":         ["4754701", "3101200"],
    "movel":        ["4754701", "3101200", "4649404"],
    "mobiliario":   ["4754701", "3101200"],
    "armario":      ["4754701", "3101200"],
    "poltrona":     ["4754701", "3101200"],
    "estante":      ["4754701", "3101200"],

    # Informática / eletrônicos
    "computador":   ["4751201", "4651601", "2621300"],
    "notebook":     ["4751201", "4651601"],
    "impressora":   ["4751201", "4651601"],
    "monitor":      ["4751201", "4651601"],
    "informatica":  ["4751201", "4651601"],
    "toner":        ["4751201", "4789003"],
    "cartucho":     ["4751201", "4789003"],
    "nobreak":      ["4751201", "4651601"],
    "eletronico":   ["4651601", "4753900"],

    # Material de escritório / papelaria
    "papel":        ["4761003", "4647801", "1721400"],
    "papelaria":    ["4761003", "4647801"],
    "caneta":       ["4761003", "4647801"],
    "escritorio":   ["4761003", "4647801", "4754701"],
    "escolar":      ["4761003", "4647801"],
    "cartolina":    ["4761003", "4647801"],
    "grampeador":   ["4761003", "4647801"],

    # Limpeza / higiene
    "limpeza":      ["4649408", "4772500", "2062200"],
    "higiene":      ["4772500", "4646002"],
    "detergente":   ["4649408", "2062200"],
    "sabao":        ["4649408", "2062200"],
    "desinfetante": ["4649408", "2062200"],
    "papel higienico": ["4649408", "4761003"],

    # Saúde / hospitalar / EPI
    "medicamento":  ["4771701", "4644301", "4645101"],
    "farmaco":      ["4771701", "4644301"],
    "hospitalar":   ["4645101", "4664800", "3250704"],
    "cirurgico":    ["4645101", "4664800"],
    "luva":         ["4645101", "4642701"],
    "mascara":      ["4645101", "4642701"],
    "epi":          ["4642701", "4789099"],
    "seringa":      ["4645101", "4664800"],

    # Alimentos / gêneros
    "alimento":     ["4639701", "4721102", "4729699"],
    "alimenticio":  ["4639701", "4721102"],
    "merenda":      ["4639701", "4721102"],
    "genero":       ["4639701", "4721102"],
    "cesta":        ["4639701", "4711302"],

    # Construção / ferramentas
    "construcao":   ["4744099", "4679604", "4744005"],
    "cimento":      ["4744005", "4679604"],
    "ferramenta":   ["4744005", "4672900"],
    "hidraulico":   ["4744001", "4744005"],
    "eletrico":     ["4742300", "4673700"],
    "tinta":        ["4741500", "2071100"],
    "material eletrico": ["4742300", "4673700"],

    # Veículos / combustível / pneus
    "combustivel":  ["4731800", "4681801"],
    "pneu":         ["4530703", "4541203"],
    "veiculo":      ["4511101", "4541201"],
    "autopeca":     ["4530703", "4530705"],
    "peca":         ["4530703", "4649404"],

    # Têxtil / vestuário / uniformes
    "uniforme":     ["4781400", "1412601"],
    "vestuario":    ["4781400", "1412601"],
    "fardamento":   ["4781400", "1412601"],
    "tecido":       ["4755501", "1340502"],
    "calcado":      ["4782201", "1531901"],
}


# ─── Famílias de serviços (CATSER) ─────────────────────────────────────────────
_MAPA_SERVICO: dict[str, list[str]] = {
    "limpeza":        ["8121400", "8122200"],
    "conservacao":    ["8121400", "8130300"],
    "vigilancia":     ["8011101", "8011102"],
    "seguranca":      ["8011101", "8020000"],
    "portaria":       ["8011101", "8121400"],
    "manutencao":     ["3314710", "4321500", "9511800"],
    "eletrica":       ["4321500", "4399103"],
    "hidraulica":     ["4322301", "4399103"],
    "ar condicionado": ["4322302", "3314710"],
    "informatica":    ["6209100", "9511800", "6204000"],
    "software":       ["6201501", "6202300"],
    "ti":             ["6209100", "6202300"],
    "obra":           ["4120400", "4299599"],
    "engenharia":     ["7112000", "7119703"],
    "reforma":        ["4330404", "4120400"],
    "pintura":        ["4330404"],
    "transporte":     ["4930202", "4921301"],
    "frete":          ["4930202"],
    "locacao":        ["7739099", "7711000"],
    "aluguel":        ["7739099", "7711000"],
    "veiculo":        ["7711000", "4923002"],
    "grafica":        ["1813099", "1813001"],
    "impressao":      ["1813001", "1813099"],
    "alimentacao":    ["5620101", "5620104"],
    "buffet":         ["5620102"],
    "refeicao":       ["5620101", "5620104"],
    "jardinagem":     ["8130300"],
    "dedetizacao":    ["8122200"],
    "treinamento":    ["8599604", "8599699"],
    "capacitacao":    ["8599604"],
    "consultoria":    ["7020400"],
    "contabil":       ["6920601", "6920602"],
    "juridico":       ["6911701"],
    "medico":         ["8630501", "8610101"],
    "saude":          ["8610101", "8630501"],
    "lavanderia":     ["9601701", "9601703"],
    "telefonia":      ["6110803", "6190601"],
    "internet":       ["6110803", "6190601"],
}


# CNAEs genéricos usados quando NENHUMA palavra casa, para não devolver vazio.
_FALLBACK_MATERIAL = ["4649499", "4789099", "4759899"]   # comércio varejista/atacadista geral
_FALLBACK_SERVICO = ["8299799", "7490104"]               # serviços diversos


def _norm(texto: str) -> str:
    t = unidecode(texto or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def mapear_cnaes(nome_item: str, tipo_cat: str, descricao: str = "") -> list[str]:
    """
    Converte o item escolhido (CATMAT/CATSER) em uma lista de CNAEs candidatos.

    Args:
        nome_item: nome/descrição do item selecionado.
        tipo_cat: "CATMAT" | "CATSER" (ou "material"/"servico").
        descricao: texto adicional opcional.

    Returns:
        Lista de CNAEs (7 dígitos, sem pontuação), ordenada por relevância e
        sem duplicatas. Nunca vazia (cai para fallback genérico).
    """
    eh_material = tipo_cat.upper() in ("CATMAT", "MATERIAL")
    mapa = _MAPA_MATERIAL if eh_material else _MAPA_SERVICO
    fallback = _FALLBACK_MATERIAL if eh_material else _FALLBACK_SERVICO

    texto = _norm(f"{nome_item} {descricao}")

    cnaes: list[str] = []
    vistos: set[str] = set()

    # 1) tenta expressões compostas (duas palavras) primeiro — mais específicas
    for chave, lista in mapa.items():
        if " " in chave and chave in texto:
            for c in lista:
                if c not in vistos:
                    vistos.add(c)
                    cnaes.append(c)

    # 2) depois palavras simples
    palavras = set(texto.split())
    for chave, lista in mapa.items():
        if " " in chave:
            continue
        # casa por radical: "cadeiras" casa com "cadeira"
        if any(chave in p or p in chave for p in palavras if len(p) >= 4):
            for c in lista:
                if c not in vistos:
                    vistos.add(c)
                    cnaes.append(c)

    if not cnaes:
        cnaes = list(fallback)

    return cnaes


def formatar_cnae(cnae: str) -> str:
    """Formata '4754701' como '4754-7/01' (padrão IBGE/Receita) para exibição."""
    c = re.sub(r"\D", "", cnae or "")
    if len(c) != 7:
        return cnae
    return f"{c[0:4]}-{c[4]}/{c[5:7]}"

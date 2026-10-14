"""
Algoritmo de cálculo de relevância para empresas.
Score de 0 a 100 baseado em múltiplos fatores.
"""
import re
from unidecode import unidecode
from difflib import SequenceMatcher


def lev_ratio(s1: str, s2: str) -> float:
    return SequenceMatcher(None, s1 or "", s2 or "").ratio()



# Mapeamento CATMAT/CATSER → tipos Google Places (para boost de score)
TIPOS_GOOGLE_POR_CATEGORIA = {
    # Materiais gerais
    "material": [
        "store", "hardware_store", "home_goods_store", "furniture_store",
        "electronics_store", "clothing_store", "supermarket", "shopping_mall",
        "establishment",
    ],
    # Serviços gerais
    "servico": [
        "plumber", "electrician", "general_contractor", "painter",
        "moving_company", "storage", "laundry", "car_repair",
        "establishment", "point_of_interest",
    ],
}


def _normalizar(texto: str) -> str:
    return unidecode(texto or "").lower().strip()


def _palavras_chave(nome_item: str) -> list[str]:
    """Extrai palavras relevantes (> 3 chars) do nome do item."""
    n = _normalizar(nome_item)
    return [p for p in re.split(r"\W+", n) if len(p) > 3]


def calcular_relevancia(
    empresa: dict,
    nome_item: str,
    tipo_cat: str,  # "CATMAT" | "CATSER"
    tipo_busca: str,  # "exata" | "similaridade"
) -> int:
    """
    Calcula o percentual de relevância (0–100) de uma empresa para o item buscado.

    O cálculo se ADAPTA à fonte da empresa:
      - Empresas do Google têm avaliação, tipos e reviews → esses sinais contam.
      - Empresas da Receita (sem Maps) usam CNAE compatível e correspondência de
        nome/atividade. Nesses casos o peso é redistribuído para não penalizar
        quem simplesmente não tem ficha no Google.
    """
    palavras = _palavras_chave(nome_item)
    if not palavras:
        return 50  # sem informação suficiente, score neutro

    fonte = (empresa.get("fonte") or "").lower()
    eh_receita = fonte == "receita"

    score = 0.0

    # ── 1. Palavras-chave no nome/razão social da empresa ──────────────────
    nome_emp = _normalizar(
        f"{empresa.get('nome','')} {empresa.get('razao_social','')}"
    )
    hits_nome = sum(1 for p in palavras if p in nome_emp)
    score += (hits_nome / len(palavras)) * 25

    # ── 2. Palavras-chave em site/reviews/descrição + CNAE (principal e
    #       secundários) ───────────────────────────────────────────────────
    cnaes_sec_txt = " ".join(
        (c.get("descricao") or "") for c in (empresa.get("cnaes_secundarios") or [])
    )
    textos_extra = " ".join([
        empresa.get("site", ""),
        empresa.get("descricao", ""),
        empresa.get("cnae_descricao", ""),
        cnaes_sec_txt,
        " ".join(empresa.get("reviews", [])),
    ])
    texto_extra_norm = _normalizar(textos_extra)
    hits_extra = sum(1 for p in palavras if p in texto_extra_norm)
    score += (hits_extra / len(palavras)) * 20

    if eh_receita:
        # ── Receita: CNAE compatível + empresa ativa valem no lugar dos
        #    sinais do Google (tipos/avaliação/reviews). ──────────────────
        # 3+4. Classificação compatível (peso 35): se houve CNAE casado pelo
        #      mapa, a própria presença na lista já indica compatibilidade.
        if empresa.get("cnae_principal"):
            score += 35
        # 5. Empresa ATIVA é proxy de maturidade (peso 10)
        if "ATIVA" in (empresa.get("situacao") or "").upper():
            score += 10
    else:
        # ── 3. Tipos Google Places compatíveis (peso 20) ──────────────────
        tipo_key = "material" if tipo_cat == "CATMAT" else "servico"
        tipos_esperados = TIPOS_GOOGLE_POR_CATEGORIA.get(tipo_key, [])
        tipos_empresa = empresa.get("tipos", [])
        if any(t in tipos_esperados for t in tipos_empresa):
            score += 20

        # ── 4. Avaliação Google (peso 15) ─────────────────────────────────
        avaliacao = float(empresa.get("avaliacao_google") or empresa.get("avaliacao", 0) or 0)
        score += (avaliacao / 5.0) * 15

        # ── 5. Quantidade de avaliações — proxy de maturidade (peso 10) ────
        qtd_aval = int(empresa.get("total_avaliacoes", 0) or 0)
        if qtd_aval >= 100:
            score += 10
        elif qtd_aval >= 20:
            score += 6
        elif qtd_aval >= 5:
            score += 3

    # ── 6. Similaridade Levenshtein nome empresa × nome item (peso 10) ─────
    lev = lev_ratio(nome_emp[:40], _normalizar(nome_item)[:40])
    score += lev * 10

    # ── Bônus: busca exata com palavras 100% presentes no nome ─────────────
    if tipo_busca == "exata" and hits_nome == len(palavras):
        score = min(score + 10, 100)

    # ── ALTA CONFIANÇA: já forneceu este tipo de item ao poder público ─────
    # Evidência concreta de que fornece o produto → piso alto de relevância.
    if empresa.get("ja_forneceu"):
        score = max(score, 85)

    return min(int(round(score)), 100)


def classificar_empresas(
    empresas: list[dict],
    nome_item: str,
    tipo_cat: str,
    tipo_busca: str,
) -> list[dict]:
    """
    Aplica o cálculo de relevância em todas as empresas e ordena por score.
    """
    for emp in empresas:
        emp["relevancia"] = calcular_relevancia(emp, nome_item, tipo_cat, tipo_busca)

    empresas.sort(key=lambda e: e["relevancia"], reverse=True)
    return empresas

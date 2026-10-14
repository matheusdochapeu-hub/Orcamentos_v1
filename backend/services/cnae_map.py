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
    "bota":         ["4782201", "1531901"],

    # Mobiliário escolar / infantil
    "berco":        ["4754701", "3101200"],
    "colchao":      ["4754701", "3104700"],

    # Eletrodomésticos / linha branca
    "geladeira":    ["4753900", "2751100"],
    "refrigerador": ["4753900", "2751100"],
    "fogao":        ["4753900", "2751100"],
    "ventilador":   ["4753900", "2751100"],
    "ar":           ["4753900", "4322302"],
    "climatizador": ["4753900", "4322302"],
    "bebedouro":    ["4753900", "4649499"],
    "eletrodomestico": ["4753900"],

    # Pneus/baterias/lubrificantes (frota)
    "bateria":      ["4530703", "4664800"],
    "oleo":         ["4681805", "4732600"],
    "lubrificante": ["4681805", "4732600"],

    # Material gráfico / sinalização
    "banner":       ["1813099", "1813001"],
    "placa":        ["1813099", "2599399"],
    "cracha":       ["1813099", "1813001"],
    "carimbo":      ["4761003", "1813099"],

    # Material elétrico detalhado / iluminação
    "lampada":      ["4742300", "4673700"],
    "luminaria":    ["4742300", "4753900"],
    "cabo":         ["4673700", "4742300"],
    "disjuntor":    ["4742300", "4673700"],

    # Insumos de laboratório / químicos
    "reagente":     ["4644301", "4664800"],
    "laboratorio":  ["4664800", "4645101"],
    "quimico":      ["4684201", "4664800"],

    # Pet / zoonoses / agropecuária
    "racao":        ["4623108", "4789004"],
    "veterinario":  ["4771704", "4789004"],
    "agricola":     ["4661300", "4692300"],
    "semente":      ["4692300", "4661300"],
    "ferramenta agricola": ["4661300", "4744005"],

    # Mobiliário urbano / segurança / incêndio
    "extintor":     ["4789099", "8020001"],
    "sinalizacao":  ["2599399", "1813099"],
    "epi completo": ["4642701"],
    "capacete":     ["4642701", "4789099"],

    # Esporte / lazer
    "esportivo":    ["4763601", "3230200"],
    "brinquedo":    ["4763604", "3240001"],

    # Combustível detalhado
    "gasolina":     ["4731800", "4681801"],
    "diesel":       ["4731800", "4681801"],
    "gas":          ["4784900", "4682600"],
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

    # Mais serviços comuns em compra pública
    "coleta":         ["3811400", "3812200"],
    "residuo":        ["3811400", "3812200"],
    "lixo":           ["3811400", "3812200"],
    "esgoto":         ["3600602", "3701100"],
    "agua":           ["3600601"],
    "cftv":           ["8020001", "4321500"],
    "monitoramento":  ["8020001", "8020002"],
    "alarme":         ["8020001", "8020002"],
    "elevador":       ["4329101", "3314710"],
    "gerador":        ["3314710", "4321500"],
    "topografia":     ["7119701"],
    "laudo":          ["7119703", "7120100"],
    "projeto":        ["7111100", "7112000"],
    "arquitetura":    ["7111100"],
    "evento":         ["8230001", "8230002"],
    "cerimonial":     ["8230001"],
    "fotografia":     ["7420001", "7420004"],
    "filmagem":       ["5911199", "7420004"],
    "publicidade":    ["7311400", "7319002"],
    "comunicacao":    ["7311400", "6391700"],
    "traducao":       ["7490103"],
    "pesquisa":       ["7320300", "7210000"],
    "recrutamento":   ["7810800", "7820500"],
    "estagio":        ["7810800"],
    "motorista":      ["4921301", "4923002"],
    "ambulancia":     ["8621601", "8622400"],
    "guincho":        ["5229002"],
    "funerario":      ["9603301", "9603302"],
    "copa":           ["5620101", "8121400"],
    "recepcao":       ["8211300", "8219999"],
    "digitalizacao":  ["8219999", "6209100"],
    "contrato":       ["6911701", "7020400"],
    "auditoria":      ["6920601", "7020400"],
    "banco de dados": ["6311900", "6204000"],
    "nuvem":          ["6311900", "6204000"],
    "hospedagem":     ["6311900", "5510801"],
    "hotel":          ["5510801", "5510802"],
    "passagem":       ["7911200", "7912100"],
    "viagem":         ["7911200", "7912100"],
    "combate incendio": ["8020001", "8011102"],
    "bombeiro":       ["8011102", "8020001"],
    "piscina":        ["8130300", "4322303"],
    "poda":           ["8130300"],
    "pintura predial": ["4330404"],
    "serralheria":    ["2512800", "4330402"],
    "marcenaria":     ["3101200", "4330404"],
    "vidracaria":     ["4330405", "2311700"],
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

    # 2) depois palavras simples (casa por radical, evitando falsos positivos
    #    de chaves muito curtas tipo "ar" dentro de "para")
    palavras = set(texto.split())
    for chave, lista in mapa.items():
        if " " in chave:
            continue
        casou = False
        for p in palavras:
            if len(p) < 3:
                continue
            if p == chave:
                casou = True
                break
            # radical só vale para chaves/palavras com 4+ caracteres
            if len(chave) >= 4 and len(p) >= 4 and (chave in p or p in chave):
                casou = True
                break
        if casou:
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

<div align="center">

[![English](https://img.shields.io/badge/EN-English-6b7280?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-2563eb?style=flat-square)](README_FR.md)

# Microsoft Agent Framework × NeoMundi

**Une mesure comportementale signée de chaque appel au modèle que fait votre agent, depuis un seul middleware.**

**[Démarrage](#démarrage)** · **[Ce qu'il refuse de faire](#refus)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limites](LIMITATIONS.md)** · **[Coût](#ce-que-ça-coûte)**

</div>

---

> **Niveau de preuve E1.** Tests hors ligne, verts sur plusieurs
> plateformes, pilotant le vrai pipeline de middleware du framework et
> son vrai connecteur sur un transport simulé. **Aucune exécution réelle
> contre l'API de mesure de production n'a encore eu lieu**, donc aucune
> version de mesure n'est déclarée vérifiée. Voir
> [LIMITATIONS §10](LIMITATIONS.md#10-evidence-level-e1).

## Ce que c'est

[Microsoft Agent Framework](https://learn.microsoft.com/fr-fr/agent-framework/overview/)
succède à Semantic Kernel et AutoGen. Il expose une couche
`ChatMiddleware` qui **encadre** chaque appel au modèle.

Ce paquet y place une mesure NeoMundi. Attachez-le une fois à un client
de conversation et chaque appel qu'il fait est mesuré — avec le décompte
de jetons du fournisseur et la latence que l'appel a réellement prise.

```
votre agent  ──▶  ChatMiddleware  ──▶  le fournisseur
                        │
                        └──▶  NeoMundi : 5 signaux, avec provenance
```

## Installation

🟡 **Pas encore publié sur PyPI.** `pip install agent-framework-neomundi`
ne fonctionne pas aujourd'hui : le paquet est en E1 et la publication
attend une qualification réelle. Installation depuis ce dépôt :

```bash
pip install "git+https://github.com/neomundi-io/neomundi-integrations.git#subdirectory=integrations/agent-framework"
```

Python 3.11 ou plus récent. Deux dépendances : `agent-framework-core` et
`neomundi`.

🟢 **Pas le méta-paquet.** `pip install agent-framework` en tire une
trentaine — Copilot Studio, Foundry, Bedrock, Redis, un bac à sable WASM.
Ce middleware n'en a besoin d'aucun : il dépend de `agent-framework-core`
seul.

## Démarrage

```python
from agent_framework.openai import OpenAIChatCompletionClient
from agent_framework_neomundi import NeoMundiChatMiddleware
from neomundi import NeoMundiClient

mesure = NeoMundiChatMiddleware(
    client=NeoMundiClient(api_key=..., base_url="https://api.neomundi.io"),
)

chat = OpenAIChatCompletionClient(model="gpt-4o-mini", middleware=[mesure])

reponse = await chat.get_response("Quelle est la capitale de la France ?")

issue = mesure.last_outcome
print(issue.status)  # submitted, ou le refus nommé
print(issue.request_id)  # votre clé de corrélation
print(issue.token_count)  # le nombre du fournisseur, jamais le nôtre
```

Rien d'autre ne change dans votre application. La réponse arrive à votre
code exactement comme avant, et avant toute mesure.

## Refus

🔴 **Les refus sont la partie qui mérite d'être lue.** Un produit de
mesure qui comble un trou en silence vaut moins qu'un produit qui dit
qu'il ne peut pas mesurer.

| Statut | Quand |
|---|---|
| `not_submitted_no_token_count` | le fournisseur n'en a publié aucun, donc le framework a laissé `usage_details` absent. **Nous n'en inventons pas.** |
| `not_submitted_invalid_token_count` | `usage_details` était là mais sans entier positif exploitable |
| `not_submitted_streaming` | un appel en flux : l'usage n'existe qu'une fois le flux consommé, et ce middleware reprend la main avant |
| `not_submitted_local_only` | `submit=False`, ou aucun client — rien n'a quitté le processus |
| `not_submitted_no_result` | `call_next()` n'a rien rendu d'exploitable |
| `not_submitted_application_error` | **votre** appel a échoué. Consigné, puis relancé — l'avaler vous cacherait une panne |
| `not_submitted_transport_error` | l'API de mesure était injoignable ou a refusé |
| `not_submitted_internal_error` | nous avons échoué. Rapporté, jamais levé dans votre application |

Exécutez-les : `python examples/02_refusals.py` fait quatre appels réels
et affiche le nombre facturé — zéro.

## Trois choses mesurées, pas supposées

🟢 **Agent Framework n'invente pas de décompte.** Le fournisseur publie
`11/7/18` → `usage_details` le porte. Le fournisseur ne publie rien →
`usage_details` vaut `None`. Les deux cas, en flux et hors flux, sur
1.26.0.

🔴 **Mais nous ne pouvons pas le vérifier appel par appel.** La charge
brute du fournisseur n'est pas atteignable à ce point d'accroche : le
contrôle que fait `litellm-neomundi` est donc impossible ici. Le décompte
est celui du framework, d'un framework mesuré comme n'en fabriquant pas —
c'est plus faible, et
[LIMITATIONS §1](LIMITATIONS.md#1--the-token-count-is-the-frameworks-and-it-is-not-verified-per-call)
le dit en entier.

🔴 **Une exception de middleware atteint l'hôte.** Le framework n'isole
pas les pannes de middleware : cette intégration attrape donc tout
elle-même. Quatre modes de défaillance distincts sont testés.

## Ce que ça coûte

Chaque observation soumise est une mesure **facturée**. Il n'existe
aucune route de traçage seul.

🟢 Les refus ne coûtent rien : aucune connexion n'est ouverte vers l'API
de mesure. Il n'y a pas d'échantillonnage en 0.1.0 — choisissez donc ce
à quoi vous l'attachez.

## Ce que ce n'est pas

- **Ni Copilot Studio ni Microsoft Foundry.** Des connecteurs vers les
  deux existent dans le framework ; qu'un appel qui passe par eux fasse
  apparaître l'usage à cette couche est **non testé**.
  [LIMITATIONS §7](LIMITATIONS.md#7--this-does-not-cover-copilot-studio-or-microsoft-foundry).
- **Pas une mesure des exécutions d'agent ni des appels d'outils.**
  Seulement les invocations de conversation. Une exécution d'agent avec
  trois appels au modèle rend trois issues, et aucun agrégat.
- **Pas vérifié sur tous les connecteurs.** Un seul a été sondé : OpenAI
  Chat Completions.
  [LIMITATIONS §2](LIMITATIONS.md#2--only-one-connector-was-measured).
- **Pas un vérificateur de vérité.** NeoMundi vérifie l'intégrité et
  l'authenticité, pas l'exactitude d'une réponse.

## Pour aller plus loin

[Limites](LIMITATIONS.md) · [Valeur et ROI](VALUE.md) ·
[Notebook](notebooks/quickstart.ipynb) · [Exemples](examples/) ·
[Journal des modifications](CHANGELOG.md) ·
[Bibliothèque d'intégrations](../../README_FR.md)

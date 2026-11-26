<div align="center">

[![English](https://img.shields.io/badge/EN-English-6b7280?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-2563eb?style=flat-square)](README_FR.md)

# LiteLLM × NeoMundi

**Mesurer chaque complétion qui passe par LiteLLM — dans votre application ou dans le Proxy — avec un seul logger, et jamais un décompte de jetons que le fournisseur n'a pas publié.**

**[Démarrage](#démarrage)** · **[Ce qu'il refuse](#refus)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limites](LIMITATIONS.md)** · **[Coût](#ce-que-ça-coûte)**

</div>

---

> **Niveau de preuve : E1 — hors ligne uniquement.** Aucune mesure réelle
> n'a encore été prise avec ce paquet : la fenêtre qui l'a construit ne
> disposait pas de la clé du compte de vérification NeoMundi. La suite
> pilote le vrai `litellm.completion` et le vrai client `neomundi` à travers
> des transports simulés. Voir [LIMITATIONS.md §9](LIMITATIONS.md#9-evidence-level-e1-not-e2).

## Ce que c'est

`NeoMundiLogger` est un `CustomLogger` LiteLLM. Attaché une fois, il soumet
chaque complétion que LiteLLM rapporte à `POST /v1/govern`, avec la latence
que LiteLLM a observée autour de l'appel et le décompte de jetons **publié
par le fournisseur**. Il pilote le SDK `neomundi` et n'en réimplémente rien.

Ce qu'il ajoute à un logger ordinaire : quand le fournisseur ne publie
rien, LiteLLM remplit l'usage avec `0` ou avec l'estimation de son propre
tokenizer, indiscernables d'un vrai décompte. Ce logger vérifie chaque
décompte dans la charge brute du fournisseur et **refuse** ce qu'il n'y
retrouve pas.

## Ce que ce n'est pas

- **Pas un évaluateur.** Il transporte des mesures ; il ne les interprète pas.
- **Pas un traceur.** Il n'existe pas de route de traçage seul : chaque soumission est une observation facturée.
- **Pas un vérificateur de vérité.** NeoMundi vérifie l'intégrité et l'authenticité, pas l'exactitude d'une réponse.
- **Pas un compteur de coût.** Le `response_cost` de LiteLLM vient d'une grille tarifaire ; il est rapporté, et non envoyé sauf demande explicite.

## Installation

Pas encore sur PyPI — `pip install litellm-neomundi` ne fonctionne pas.
Installation depuis le dépôt :

```bash
pip install 'git+https://github.com/neomundi-io/neomundi-integrations.git#subdirectory=integrations/litellm'
```

Python ≥ 3.11. Dépend de `litellm>=1.111.0,<2.0` et `neomundi>=2.1.0,<3.0`.

## Démarrage

```python
logger = NeoMundiLogger(api_key=..., base_url="https://api.neomundi.io")
litellm.callbacks = [logger]
answer = litellm.completion(model="ollama_chat/llama3.2", messages=messages)
```

Gratuit d'abord : `NeoMundiLogger(submit=False)` ne demande aucune clé,
n'ouvre aucune socket et exécute toutes les vérifications — vous voyez
combien de vos complétions seraient mesurées avant de dépenser quoi que ce
soit.

Dans le **Proxy LiteLLM**, placez une instance dans un module et
référencez-la sous `litellm_settings.callbacks` dans `config.yaml`. Lisez
d'abord [LIMITATIONS §2](LIMITATIONS.md#2-async-streams-are-never-measured) :
les flux asynchrones sont refusés.

## Refus

| Situation | Statut | Envoyé à NeoMundi |
|---|---|---|
| Le fournisseur n'a publié aucun décompte (LiteLLM dit `0` ou estime) | `not_submitted_unverified_token_count` | rien |
| Flux asynchrone : la charge du fournisseur est hors de portée du logger | `not_submitted_unverified_token_count` | rien |
| LiteLLM n'a fourni aucun début/fin exploitable | `not_submitted_no_latency` | rien |
| LiteLLM a répondu depuis son cache | `not_submitted_cache_hit` | rien |
| Votre appel LLM a échoué | `not_submitted_application_error` | rien ; votre exception est intacte |
| Embedding, image ou autre appel non conversationnel | `not_submitted_unsupported_call` | rien |
| `sample_rate` a exclu l'appel | `skipped_sampling` | rien |
| `submit=False` | `not_submitted_local_only` | rien |
| L'API NeoMundi a échoué ou est injoignable | `not_submitted_transport_error` | tenté ; votre appel est intact |

Aucune exception de ce logger n'atteint votre application — testé sous huit
pannes d'API distinctes et des entrées malformées.

## Ce que ça coûte

🔴 **Chaque observation soumise est une mesure facturée** (0,030 CHF en
TIER_03). `sample_rate` vaut `1.0` par défaut, et une passerelle voit tous
les appels de toutes les applications derrière elle. Baissez-le avant la
mise en service ; la décision est déterministe en `litellm_call_id`.

**Coupe-circuit :** `logger.sample_rate = 0.0` — immédiat. Ne comptez pas
sur `litellm.callbacks = []` : LiteLLM garde le logger enregistré après
cela ([LIMITATIONS §4](LIMITATIONS.md#4-replacing-or-removing-the-logger-is-not-what-it-looks-like)).

## État, honnêtement

| | |
|---|---|
| Suite hors ligne | 47 tests, verts sur Python 3.12 / Linux en local ; la CI couvre 3.11–3.13 sur Linux, macOS, Windows |
| Exécution réelle | **aucune** — E1. 0 observation soumise, 0 relue |
| Maturité | expérimentale |

Lisez [LIMITATIONS.md](LIMITATIONS.md) avant de tirer une conclusion d'un score.

## Licence

Apache-2.0. Voir [LICENSE](../../LICENSE).

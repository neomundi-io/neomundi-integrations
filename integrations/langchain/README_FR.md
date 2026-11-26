<div align="center">

[![English](https://img.shields.io/badge/EN-English-2563eb?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-2563eb?style=flat-square)](README_FR.md)

# LangChain × NeoMundi

**Mesurer ce que votre application LangChain fait réellement à l'exécution.**

Un seul gestionnaire de rappels. Cinq signaux mesurés par interaction. Des preuves vérifiables.

**[Démarrage](#démarrage)** · **[Ce qu'il refuse de faire](#les-quatre-refus)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limites](KNOWN_LIMITATIONS.md)** · **[Coût](#ce-que-cela-coûte)**

[SDK NeoMundi](https://github.com/neomundi-io/neomundi-python-sdk) · [Architecture produit](https://github.com/neomundi-io/.github/blob/main/docs/PRODUCT_ARCHITECTURE.fr.md) · [Créer un compte](https://controltower.neomundi.io/welcome)

</div>

---

## Ce que c'est

`langchain-neomundi` relie l'[API de mesure NeoMundi](https://github.com/neomundi-io/neomundi-python-sdk) à une application LangChain par un gestionnaire de rappels standard. Chaque interaction LLM de votre chaîne devient une observation mesurée : cinq signaux, chacun avec sa provenance et un état explicite `mesuré` / `non mesuré`.

C'est une enveloppe mince autour de `client.trace()`, la brique du SDK. Le SDK mesure la latence, fait respecter le contrat de requête et soumet l'observation. **Rien n'est réimplémenté ici** — c'est pourquoi ce paquet reste assez petit pour être audité d'une seule traite.

```
votre chaîne ──▶ rappel LangChain ──▶ client.trace() ──▶ POST /v1/govern
                  (ce paquet)          (SDK neomundi)     (API de mesure)
```

## Ce que ce n'est pas

- **Pas un évaluateur.** Il transporte les mesures, il ne les interprète pas. Aucun seuil, aucun verdict, pas de `is_safe()`. Cette frontière est héritée du SDK, à dessein.
- **Pas un traceur.** Il n'existe aucune route de traçage seul sur l'API NeoMundi. Refermer une mesure **est** une observation facturée. Voir [ce que cela coûte](#ce-que-cela-coûte).
- **Pas un vérificateur de vérité.** NeoMundi vérifie l'intégrité et l'authenticité d'un enregistrement. Il **ne** vérifie **pas** que la réponse du modèle était vraie.

## Installation

```bash
pip install langchain-neomundi
```

Python 3.11 ou plus récent — ce plancher vient de LangChain 1.x, pas de NeoMundi. Deux dépendances : `langchain-core` et `neomundi`.

## Démarrage

```python
from langchain_neomundi import NeoMundiCallbackHandler

handler = NeoMundiCallbackHandler(
    api_key="ct_live_...",  # votre clé NeoMundi
    base_url="https://api.neomundi.io",  # obligatoire : aucun hôte n'est supposé
)

reponse = chain.invoke(
    {"question": "Qu'a changé notre politique de remboursement ?"},
    config={"callbacks": [handler]},
)

resultat = handler.last_outcome
print(resultat.status)  # 'submitted'
print(resultat.request_id)  # l'identifiant serveur — votre seule clé de corrélation
print(resultat.latency_ms)  # mesurée, pas déclarée
```

Branchez-le d'abord gratuitement. `submit=False` mesure la latence localement et n'ouvre aucune socket :

```python
handler = NeoMundiCallbackHandler(client, submit=False)
```

### Chaînes RAG

Les documents récupérés sont repris automatiquement depuis `on_retriever_end` et attachés à la mesure comme `rag_context`, pour que les signaux d'ancrage aient de quoi travailler. Aucun câblage supplémentaire :

```python
chain = {"context": retriever | format_docs, "question": RunnablePassthrough()} | prompt | model
chain.invoke(question, config={"callbacks": [handler]})
```

## Les quatre refus

C'est la partie qui mérite d'être lue. Une couche d'instrumentation qui devine, ou qui échoue en silence, produit des mesures fabriquées — ce qui est pire que de ne rien mesurer. Donc :

| Situation | Ce qui se passe | Statut |
|---|---|---|
| Le fournisseur ne publie aucun compte de jetons | **Rien n'est soumis.** Aucune estimation : l'API ne compte rien sur cette route, et un compte inventé serait une mesure fabriquée. | `not_submitted_no_token_count` |
| Votre propre appel LLM a échoué | **Rien n'est soumis.** Il n'y a pas d'interaction complète à mesurer. | `not_submitted_application_error` |
| L'API de mesure est tombée, en 401, 402 ou 429 | **Votre chaîne n'est pas affectée** et rend sa réponse. L'échec est rapporté, jamais levé. | `not_submitted_transport_error` |
| L'échantillonnage a écarté l'exécution | Aucune socket, rien de facturé. | `skipped_sampling` |

Chaque décision produit un `Outcome`. Rien n'est silencieux :

```python
handler = NeoMundiCallbackHandler(..., on_outcome=mon_collecteur)

for resultat in handler.outcomes:
    print(resultat.status, resultat.submitted, resultat.request_id)
```

**Aucune exception de ce gestionnaire, du SDK ou du réseau n'atteint jamais votre chaîne.** C'est éprouvé sur une vraie chaîne LCEL sous six pannes d'API distinctes — voir `tests/test_never_raises.py`.

## Ce que cela coûte

🔴 **Chaque observation soumise est une mesure facturée.** Il n'existe aucune route de traçage seul : refermer une mesure émet `POST /v1/govern`.

Avec `sample_rate=1.0` par défaut, **chaque appel LLM de votre application est facturé.** Sur un compte TIER_03, cela représente 0,030 CHF par appel. Une application servant 10 000 appels par jour en facturerait 10 000.

L'échantillonnage est donc un contrôle de premier plan, pas un détail :

```python
# Mesurer un appel sur vingt.
handler = NeoMundiCallbackHandler(..., sample_rate=0.05)
```

La décision est **déterministe selon l'identifiant d'exécution** : un échantillon est reproductible, et non un tirage que l'on ne peut pas rejouer. `sample_rate=0.0` n'ouvre aucune socket, ce qui en fait un véritable coupe-circuit.

`handler.submitted_count` dit combien d'observations ont réellement été facturées.

## État réel, sans enjolivure

| | |
|---|---|
| Suite hors ligne | **54 tests**, verts sur Python 3.11–3.13. Aucun réseau, aucune clé, aucun coût. |
| Exécution réelle | **6 observations** soumises à `https://api.neomundi.io` et **6/6 relues comme persistées**, depuis une vraie chaîne LangChain pilotant un vrai LLM local. Voir [`evidence/`](evidence/). |
| Version de mesure | La production sert actuellement **3.5.0**, que le SDK n'a **délibérément pas** encore qualifiée. Le gestionnaire avertit une fois par instance. Voir [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md). |
| Maturité | **Alpha.** La surface de rappels est stable ; le paquet est neuf. |

Lire [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) avant de tirer une conclusion d'un score. C'est court, et ce n'est pas de la communication.

Comment cela a été construit, ce qui a cassé en route et ce que cela a coûté : [docs/MISSION_REPORT.md](docs/MISSION_REPORT.md). Pourquoi cela vaut la dépense : [docs/VALUE.md](docs/VALUE.md).

## Référence

### `NeoMundiCallbackHandler`

| Paramètre | Défaut | Sens |
|---|---|---|
| `client` | — | Un `neomundi.NeoMundiClient` existant. Exclusif avec les deux suivants. |
| `api_key`, `base_url` | — | Construire un client ici. Les deux sont requis : le SDK ne suppose jamais d'hôte. |
| `sample_rate` | `1.0` | Fraction des exécutions soumises. Déterministe selon l'identifiant. |
| `submit` | `True` | `False` mesure localement et n'ouvre aucune socket. |
| `mode` | `"OBS"` | Mode de mesure transmis à l'API. |
| `metadata` | `None` | Fusionné dans les métadonnées de l'observation. |
| `capture_prompt` / `capture_response` | `True` | `False` garde le contenu dans votre processus. |
| `on_outcome` | `None` | Appelé avec chaque `Outcome`. Ses propres exceptions sont interceptées. |
| `max_outcomes` | `256` | Borne de l'historique conservé. |
| `raise_errors` | `False` | `True` relève au lieu de rapporter. Pour le débogage seulement. |

### `Outcome`

`run_id`, `status`, `submitted`, `request_id`, `latency_ms`, `token_count`, `model_name`, `measurement_version`, `reason`, `error_type`.

`request_id` est la **seule** clé de corrélation fiable contre l'API. `run_id` est celui de LangChain ; le serveur ne le conserve pas comme identité. `error_type` porte le nom de classe de l'exception et jamais son message, qui pourrait contenir un prompt.

## Exécuter les tests

```bash
pip install -e ".[dev]"
pytest
```

Les exemples pilotent un modèle Ollama local, **facultatif** et qui n'est pas
une dépendance du paquet — le gestionnaire est agnostique du fournisseur.
Pour les exécuter :

```bash
pip install -e ".[dev,examples]"
```

Hors ligne par conception : chaque test passe par `httpx.MockTransport`, et l'URL de base est `https://example.invalid` (RFC 2606), pour qu'un transport simulé oublié échoue sur le DNS plutôt que d'atteindre un serveur.

Pour reproduire la preuve d'exécution réelle — **cela dépense de l'argent** :

```bash
python scripts/real_execution_proof.py --dry-run     # gratuit
python scripts/real_execution_proof.py --max-billed 10
```

## Licence

Apache-2.0. Voir [LICENSE](LICENSE).

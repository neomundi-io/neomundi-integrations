# Contribuer une intégration

[English](CONTRIBUTING.md) · [Index](README_FR.md) · [Catalogue](CATALOG_FR.md)

Une intégration est acceptée quand elle est **utile, honnête et prouvable**.
Ce document dit précisément ce que ces trois mots veulent dire ici, pour que
personne n'ait à deviner ni à débattre.

---

## La forme d'une intégration

Chaque intégration vit dans `integrations/<slug>/` et porte les mêmes
fichiers. L'uniformité n'est pas de la bureaucratie : c'est la seule chose
qui garde navigable une bibliothèque de cent entrées, et elle est **imposée
par `tools/check_conformance.py`**, pas espérée.

```
integrations/<slug>/
├── INTEGRATION.yaml     la source de vérité de cette intégration
├── README.md            le point d'entrée du développeur
├── README_FR.md         le même, en français
├── LIMITATIONS.md       ce qu'elle ne fait pas, mesuré et non supposé
├── VALUE.md             complémentarité, valeur métier, arithmétique du ROI
├── CHANGELOG.md         par paquet, SemVer
├── pyproject.toml       ses dépendances et son plancher Python
├── src/<nom_import>/    le code, avec py.typed
├── tests/               hors ligne, sans clé, sans coût
├── examples/            exécutables, et honnêtes sur ce qu'ils démontrent
├── notebooks/           au moins un notebook reproductible
└── evidence/            exigé dès E2
```

Partir de `templates/integration/`. **Ne pas copier une autre
intégration** : on hérite de sa formulation et de ses hypothèses.

## Les deux axes, et pourquoi ils sont séparés

**Maturité** — le cycle de vie du paquet : `experimental` → `beta` →
`stable` → `maintained` → `deprecated` → `archived`. Il dit à quel point
faire confiance au code.

**Preuve** — ce qui a réellement été démontré : **E0** rien encore, **E1**
tests hors ligne verts sur plusieurs plateformes, **E2** + exécution réelle
contre la production avec preuve versionnée, **E3** + reproduction par un
tiers indépendant.

Séparés, parce qu'un paquet mûr peut être mal prouvé et qu'un paquet bien
prouvé peut rester alpha. L'échelle est celle de
[`neomundi-use-cases`](https://github.com/neomundi-io/neomundi-use-cases/blob/main/CATALOG_FR.md),
**réutilisée et non redéfinie** — deux échelles de maturité concurrentes
dans une même organisation finissent toutes les deux fausses.

### Le niveau de preuve est calculé, pas déclaré

C'est ce qui rend le catalogue digne d'être lu.

Si `INTEGRATION.yaml` annonce `evidence.level: e2`, alors
`check_conformance.py` ouvre le fichier nommé par `evidence.proof` et
vérifie qu'il rapporte au moins une observation **soumise**, que
`real_observations` correspond au nombre d'enregistrements soumis, et que
`observations_verified_persisted` correspond au nombre relu comme persisté.
Un écart fait échouer la CI.

Le badge de l'index est donc un **fait**. On ne promeut pas sa propre
intégration en modifiant un champ.

## Critères de recette

Les écrire **avant** d'implémenter. Sur la première mission, c'est ce qui a
rendu un vrai défaut indiscutable au lieu de discutable.

Huit critères sont exigés de toute intégration ; ajouter les vôtres par
dessus.

1. Une interaction mesurée produit une observation persistée, et son
   `request_id` est relu depuis l'API.
2. Une interaction non mesurable **ne soumet rien et n'invente rien**, et
   dit pourquoi.
3. **Aucune exception de l'intégration n'atteint jamais l'application
   hôte** — ni la vôtre, ni celle du SDK, ni celle du réseau. À éprouver
   sous au moins quatre pannes d'API distinctes.
4. La latence rapportée est celle de l'appel de l'application, pas celle de
   la mesure.
5. Échantillonnage à zéro : aucune socket ouverte, rien de facturé.
6. La suite entière tourne **hors ligne, sans clé et sans coût**.
7. Le notebook s'exécute de bout en bout sur une machine neuve, à partir du
   seul README.
8. Aucune clé, aucun prompt, aucune réponse dans un journal, une issue ou
   un message d'erreur.

Le critère 3 décide si une intégration est recommandable. Une
instrumentation capable de faire tomber l'application qu'elle observe a une
valeur négative.

## Les trois refus, hérités

Ils viennent du SDK `neomundi` et une intégration ne peut pas les adoucir.

- **Rien n'est inventé.** Sans compte de jetons publié par l'hôte, aucune
  observation n'est soumise. Une estimation serait une mesure fabriquée,
  attribuée à l'application de quelqu'un.
- **Rien ne s'échappe.** Aucune exception n'atteint l'hôte.
- **Rien n'est silencieux.** Chaque exécution rend une issue explicite.

## Règles d'honnêteté imposées par la CI

- Un paquet en `published_on: none` ne peut pas offrir un
  `pip install <nom>` **copiable** qui échouerait. Une phrase expliquant
  que la commande ne marche pas encore est encouragée ; un bloc de code que
  le lecteur copierait est refusé.
- `LIMITATIONS.md` ne peut pas être un fichier vide ou un gabarit.
- Un préfixe de clé vivante, où que ce soit dans l'arbre, fait échouer la
  construction.

## Coût

🔴 **Chaque observation soumise est une mesure facturée.** Il n'existe
aucune route de traçage seul sur l'API NeoMundi.

Conséquences de conception, non optionnelles :

- exposer un contrôle d'échantillonnage, et en parler en premier dans la
  section coût du README ;
- offrir un mode qui mesure localement sans ouvrir de socket, pour qu'un
  développeur puisse brancher l'intégration gratuitement avant de dépenser ;
- rendre la décision d'échantillonnage **déterministe selon l'identifiant
  d'exécution**, pour qu'un échantillon soit reproductible et non un tirage
  que personne ne peut rejouer.

Pour la preuve d'exécution : compte de service de vérification, garde-fou
passé **avant** de mesurer, et chaque enregistrement marqué comme technique
pour être exclu de toute analyse. Jamais un compte pilote.

## Versionnement et publication

- SemVer par paquet, indépendant de toutes les autres intégrations.
- Étiquettes `<slug>-v<version>`, par exemple `langchain-v0.1.0`.
- La publication est un **acte distinct et approuvé** : dispatch manuel
  depuis `main`, étiquette existante et concordante, chaîne de confirmation,
  approbation d'environnement. Jamais un effet de bord d'une fusion.

## Ouvrir la pull request

Une intégration, une PR. La CI dira, dans cet ordre, si le squelette est
incomplet, si une affirmation de preuve est invérifiable, si l'index généré
est périmé, ou si un test échoue. Lancer les contrôles en local d'abord :

```bash
python -m pip install -r tools/requirements.txt
python tools/check_conformance.py
python tools/render_index.py          # régénère index et badges
cd integrations/<slug> && pytest -q
```

Committer l'index régénéré — `README.md`, `README_FR.md`, `CATALOG.md`,
`CATALOG_FR.md`, `registry.json` et `badges/` — avec le travail. La CI
échoue s'il est périmé, car un index qui dérive de la réalité est pire que
pas d'index.

## Déprécier et archiver

Déprécier explicitement : `maturity: deprecated` plus un `DEPRECATION.md`
disant pourquoi, depuis quand, et vers quoi migrer.

Archiver en déplaçant vers `integrations/_archived/<slug>/`. **Rien n'est
jamais supprimé** — une preuve doit survivre au code qu'elle atteste.

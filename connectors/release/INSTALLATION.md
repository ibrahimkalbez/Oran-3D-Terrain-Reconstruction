# Installation — connecteurs Claude pour Rhino / Grasshopper / ANSYS

| Fichier | Où l'installer | Rôle |
|---|---|---|
| `RhinoMcpBridge.rhp` (v1.1.0) | Rhino 8 (Windows) | Plug-in : passerelle locale sécurisée vers Rhino et Grasshopper (nécessaire aux deux connecteurs) |
| `FusionRhinoGrasshopperAnsys-1.0.0.mcpb` | Claude Desktop | **Connecteur 2** (58 outils) : tout le connecteur 1 + règles d'urbanisme, variantes, arbres, simulations / ANSYS |
| `RhinoGrasshopperConnector-1.0.1.mcpb` | Claude Desktop | **Connecteur 1** seul (39 outils) : Rhino, Grasshopper, variantes |

Installez le plug-in, puis **un seul** des deux connecteurs : le Fusion contient déjà tout le connecteur 1
(si vous aviez installé le connecteur 1, désactivez-le dans Claude Desktop pour éviter les outils en double).

Prérequis : Windows 10/11, **Rhino 8** (n'importe quelle version 8.x) et **Claude Desktop** à jour. Rien d'autre à installer : ni Python, ni Node.js.
ANSYS (Workbench, Fluent, Mechanical…) n'est nécessaire que pour les simulations ANSYS du connecteur 2.

> **Mise à jour depuis la version 1.0.0 du plug-in** : fermez Rhino, remplacez `RhinoMcpBridge.rhp` dans son
> dossier permanent par la version 1.1.0, débloquez-le (étape 1.2) et rouvrez Rhino. `McpBridgeStatus` doit
> afficher `1.1.0`.

---

## 1. Installer le plug-in dans Rhino (2 minutes)

1. Copiez `RhinoMcpBridge.rhp` dans un dossier **permanent**, par exemple `Documents\RhinoMCP\plugin\`.
   Rhino charge le plug-in depuis cet emplacement : ne le laissez pas dans *Téléchargements*.
2. **Débloquez le fichier** (Windows bloque les DLL téléchargées) : clic droit sur le fichier → *Propriétés* →
   cochez **Débloquer** → *OK*.
3. Ouvrez Rhino 8 et **glissez-déposez** `RhinoMcpBridge.rhp` dans la fenêtre de Rhino.
   (Autre méthode : commande `PluginManager` → *Installer…* → choisir le fichier.)
4. Tapez `McpBridgeStatus` dans la ligne de commande. Vous devez lire :

   ```
   Rhino MCP Bridge 1.1.0: running on http://127.0.0.1:8642
   ```

Le plug-in démarre ensuite **automatiquement à chaque lancement de Rhino**.

Commandes Rhino disponibles :

| Commande | Effet |
|---|---|
| `McpBridgeStatus` | État de la passerelle, port, fichier de découverte |
| `McpBridgeStart` / `McpBridgeStop` | Démarrer / couper l'accès de Claude à ce Rhino |
| `McpBridgeSettings` | Port préféré, démarrage automatique, journal des requêtes |

## 2. Installer le connecteur dans Claude Desktop (1 minute)

1. **Double-cliquez** sur `FusionRhinoGrasshopperAnsys-1.0.0.mcpb` (ou `RhinoGrasshopperConnector-1.0.1.mcpb`).
   (Ou : Claude Desktop → *Paramètres* → *Extensions* → *Installer une extension…* → choisir le fichier.)
2. Cliquez sur **Installer**. Les réglages sont facultatifs :
   - **Dossier de travail** : où sont enregistrées les variantes (par défaut `Documents\RhinoMCP`) ;
   - **Instance Rhino préférée** : seulement si vous ouvrez plusieurs Rhino en même temps ;
   - **Délais** : à augmenter si vos définitions Grasshopper calculent plusieurs minutes.
3. Activez l'extension. Dans une nouvelle conversation, le connecteur apparaît dans le menu des outils.

## 3. Premier essai

Rhino ouvert (avec un modèle), puis écrivez à Claude :

1. « **Que contient mon modèle Rhino ?** » → unités, calques, objets, géoréférencement.
2. « **Crée une définition Grasshopper de test : un slider Building_Height de 3 à 30 m (valeur 15), une boîte de 20 × 30 m dont la hauteur vient du slider, et une sortie OUT_Volume.** »
3. « **Augmente la hauteur des bâtiments de 10 % et relance Grasshopper.** »
   Claude modifie le slider (+10 %), relance la solution et explique ce qui a changé (hauteur 15 → 16,5 m, volume +10 %…).
4. « **Crée trois variantes avec une hauteur de 12, 15 et 18 mètres.** »
   Claude calcule, capture, enregistre et compare V01, V02, V03.
5. « **Montre-moi la variante 03.** » → image et métriques de V03.
6. « **Garde la variante 02.** » → V02 marquée ★.

Avec vos propres définitions : ouvrez le fichier `.gh` (« Ouvre `C:\…\mon_projet.gh` ») et suivez le
[guide de préparation des définitions](../docs/GRASSHOPPER_CONVENTIONS.md) pour que Claude trouve
paramètres et résultats sans ambiguïté.

Avec le connecteur 2 (Fusion), essayez ensuite :

7. « **Détecte les calques du site et vérifie le règlement exemple.** » → tableau ✅/❌ et bâtiments fautifs en rouge.
8. « **Plante des ficus tous les 8 m le long des rues, en évitant les bâtiments.** »
9. « **Calcule l'ensoleillement au 21 décembre sur l'espace public.** » → carte colorée dans Rhino.
10. « **Explore la hauteur de 12 à 30 m avec le règlement comme contrainte et garde la meilleure variante.** »

Guide complet du connecteur 2 : [`docs/FUSION.md`](../docs/FUSION.md) (règles, variantes, arbres, simulations, préparation d'un projet ANSYS Workbench).

## Dépannage

| Problème | Solution |
|---|---|
| « Rhino is not reachable » | Rhino est fermé, ou le plug-in n'est pas chargé : `McpBridgeStatus`, puis `McpBridgeStart`. |
| Le plug-in ne se charge pas | Fichier non débloqué (étape 1.2), ou fichier déplacé après installation : réinstallez-le depuis son dossier permanent. |
| « Rhino did not pick up the request » | Une boîte de dialogue ou une commande est en cours dans Rhino : terminez-la (Échap). |
| Plusieurs Rhino ouverts | Demandez « statut de la connexion Rhino » : Claude liste les instances et peut changer (`select_pid`), ou réglez *Instance Rhino préférée*. |
| Le port 8642 est occupé | Le plug-in prend automatiquement le suivant (8643…8651) ; Claude le trouve tout seul. |

## Sécurité

- La passerelle n'écoute que sur `127.0.0.1` : elle est **invisible depuis le réseau**.
- Chaque requête doit présenter un **jeton aléatoire** renouvelé à chaque démarrage de Rhino, stocké dans
  `%LOCALAPPDATA%\RhinoMcpBridge\instances\` (lisible seulement par votre compte Windows).
- Les requêtes venant d'un navigateur web sont refusées (protection contre les pages malveillantes).
- Chaque modification faite par Claude est **une seule étape d'annulation** dans Rhino (`Ctrl+Z`).

## Empreintes SHA-256

```
113e0f29f1cf3694465d88d4fe6b255ab8dfdfe00302fe8db483b7a0534b8189  FusionRhinoGrasshopperAnsys-1.0.0.mcpb
98a7ab575fe098700f24dd1ed6acfa7c14ddd98a8a513320792f9b4b3418b9e2  RhinoGrasshopperConnector-1.0.1.mcpb
c9a2db5d5f5f6b94cab0b9ba4e5cdb89fc91c4b72ce6b3c2324b996e4175bf13  RhinoMcpBridge.rhp
```

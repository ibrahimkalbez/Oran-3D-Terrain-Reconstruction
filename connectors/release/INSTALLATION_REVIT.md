# Installation — connecteurs Claude pour Revit / Dynamo / ANSYS

| Fichier | Où l'installer | Rôle |
|---|---|---|
| `RevitMcpBridge-1.0.0.zip` | Revit 2022, 2023, 2024, 2025, 2026 (Windows) | Add-in : passerelle locale sécurisée vers Revit et Dynamo (nécessaire aux deux connecteurs) |
| `FusionRevitDynamoAnsys-1.0.0.mcpb` | Claude Desktop | **Fusion Revit Dynamo ANSYS** (53 outils) : tout le connecteur Revit Dynamo + règles d'urbanisme, variantes sous contraintes urbaines **et** simulations physiques (soleil, ANSYS), arbres |
| `RevitDynamoConnector-1.0.0.mcpb` | Claude Desktop | **Revit Dynamo Connector** seul (34 outils) : maquette Revit, paramètres, graphes Dynamo comme Dynamo Player, variantes |

Installez l'add-in, puis **un seul** des deux connecteurs : le Fusion contient déjà tout le Revit Dynamo Connector.

Prérequis : Windows 10/11, **Revit 2022 à 2026** avec **Dynamo for Revit** (installé d'office avec Revit),
**Claude Desktop** à jour. Rien d'autre : ni Python, ni Node.js. ANSYS n'est nécessaire que pour les simulations ANSYS.

---

## 1. Installer l'add-in dans Revit (2 minutes)

1. **Fermez Revit.**
2. Clic droit sur `RevitMcpBridge-1.0.0.zip` → *Propriétés* → cochez **Débloquer** → *OK*, puis
   *Extraire tout…* dans un dossier quelconque.
3. Dans le dossier extrait `RevitMcpBridge`, double-cliquez sur **`Installer.cmd`**.
   Le script détecte vos versions de Revit et installe la bonne version de l'add-in pour chacune
   (2022–2024 : build .NET Framework 4.8 ; 2025–2026 : build .NET 8) dans
   `%APPDATA%\Autodesk\Revit\Addins\<année>\`.
   Une seule version : `powershell -ExecutionPolicy Bypass -File Install-RevitMcpBridge.ps1 -Versions 2025`.
4. Ouvrez Revit. À la question de sécurité sur **« Revit MCP Bridge (Claude) »**, cliquez **« Toujours charger »**.
5. Onglet **Compléments** → panneau **MCP Bridge** → **Claude Bridge** : vous devez lire
   `Running on http://127.0.0.1:8742`.

L'add-in démarre ensuite **automatiquement à chaque lancement de Revit**. Le bouton **Start / Stop** coupe ou
rétablit l'accès de Claude.

## 2. Installer le connecteur dans Claude Desktop (1 minute)

1. **Double-cliquez** sur `FusionRevitDynamoAnsys-1.0.0.mcpb` (ou `RevitDynamoConnector-1.0.0.mcpb`).
   (Ou : Claude Desktop → *Paramètres* → *Extensions* → *Installer une extension…*.)
2. Cliquez sur **Installer**. Réglages facultatifs :
   - **Dossier de travail** : variantes, explorations, simulations (par défaut `Documents\RevitMCP`) ;
   - **Dossier des graphes Dynamo** : le dossier où sont vos `.dyn` (par exemple celui de Dynamo Player) ;
   - **Session Revit préférée** : seulement si plusieurs Revit sont ouverts ;
   - **Délais** : à augmenter si vos graphes calculent plusieurs minutes.
3. Activez l'extension ; dans une nouvelle conversation, le connecteur apparaît dans le menu des outils.

## 3. Premier essai

Revit ouvert avec un projet, puis écrivez à Claude :

1. « **Que contient mon projet Revit ?** » → niveaux, catégories, quantités, paramètres globaux, localisation.
2. « **Quels graphes Dynamo ai-je ?** » puis « **Que demande le graphe Massing ?** » → entrées (curseurs, bornes) et sorties.
3. « **Lance Massing avec une hauteur de 18 m et 6 niveaux, et montre-moi le résultat.** »
4. « **Augmente la hauteur de 10 %.** » → l'entrée passe de 18 à 19,8 m ; les éléments sont mis à jour, pas dupliqués.
5. « **Crée trois variantes avec 4, 6 et 8 niveaux et compare la surface de plancher.** » → V01, V02, V03 avec images.
6. « **Montre-moi la variante 02 et remets-la dans Revit.** »

Avec le Fusion :

7. « **Détecte le site et vérifie le règlement exemple sur les volumes.** »
8. « **Fais varier la hauteur de 12 à 30 m, respecte le règlement, calcule l'ensoleillement au 21 décembre et
   maximise la surface de plancher.** » → variantes filtrées par le règlement **et** la simulation, classées, enregistrées.
9. « **Plante des arbres tous les 8 m le long de la voirie.** »

Préparer vos graphes (entrées « Is Input », sorties « Is Output ») : voir [`docs/REVIT.md`](../docs/REVIT.md), § 2.

## Dépannage

| Problème | Solution |
|---|---|
| « Revit is not reachable » | Revit fermé ou add-in non chargé : *Compléments → MCP Bridge → Claude Bridge*. Pas de panneau : relancez `Installer.cmd`, puis « Toujours charger ». |
| « Revit did not pick up the request in time » | Une boîte de dialogue, un mode esquisse/édition ou une commande est en cours : terminez-la (Échap). |
| « Dynamo for Revit is not available » | Ouvrez Dynamo une fois manuellement dans cette version de Revit. |
| « Dynamo did not open the graph » | Le graphe est ouvert dans la fenêtre Dynamo avec des modifications : enregistrez-le ou fermez-le. |
| Port 8742 occupé | L'add-in prend le suivant (8743…8751) ; Claude le trouve tout seul. |

## Sécurité

- La passerelle n'écoute que sur `127.0.0.1` : **invisible depuis le réseau**.
- **Jeton aléatoire** renouvelé à chaque démarrage, stocké dans `%LOCALAPPDATA%\RevitMcpBridge\instances\`.
- Les requêtes venant d'un navigateur web sont refusées.
- Chaque modification faite par Claude est **une seule opération annulable** (« Claude: … »).

## Désinstallation

Revit fermé : `Uninstall-RevitMcpBridge.ps1` (dans le dossier extrait), puis supprimez l'extension dans Claude Desktop.

## Empreintes SHA-256

```
79acd9c79369f95c8d2730e26aebedd0c30e428a2ed86b3eaf10f2044c7fbdbf  RevitMcpBridge-1.0.0.zip
e3b78c7401db445a368319f57786dc36fea2d446188f1fa67adebb91acc80e6a  RevitDynamoConnector-1.0.0.mcpb
053ffa0d24a53ffa5185361cebc8b7ea93c2779850452e4a87bbb13e4ecb495a  FusionRevitDynamoAnsys-1.0.0.mcpb
```

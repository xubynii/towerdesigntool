# Tower Workspace

A browser workspace for early tower design in three spaces that share one project:

- **Model** – choose a site (from a list, on a 2D map, or imported), draw or import the massing, set program and material.
- **Nodes** – wire analysis checks to the model. Sun, wind, rain and energy run the Sun & Wind Studio engines; each opens as a pop-up studio. Other checks are screening estimates with editable settings.
- **Presentation** – drawing sets in four styles, renders, boards built from presentation nodes, and a written report.

There is no build step. Everything is in `index.html` plus a few files it loads on demand.

**Live:** https://xubynii.github.io/towerdesigntool/

## Run it

```
python -m http.server 8080
```

Then open `http://127.0.0.1:8080/`. It needs an internet connection for three.js, polygon-clipping and the fonts, which come from public CDNs.

Opening `index.html` straight from disk also works, with two limits: the analysis engines run on the page instead of in a worker, and the browser may block the Rhino live link.

`index.html` is written as the body of a Claude artifact page, so it has no `<!doctype>` or `<html>` wrapper. Browsers render it as it is.

## Files

| Path | What it is |
|---|---|
| `index.html` | The whole app: styles, markup and one script |
| `sw/engines.js`, `sw/worker.js` | Sun, wind, rain and energy engines with the CWEC 2020 Vancouver climate year, and the worker that runs them |
| `map/vancouver.js` | The map for "Select site boundary": blocks, buildings with heights and labels around the Vancouver Tall competition site |
| `tools/gen_map.py` | Rebuilds `map/vancouver.js` from the competition base model |
| `tools/build_site.py` | Builds the website on the `gh-pages` branch: wraps `index.html` in a normal page and copies the other files |
| `rhino/rhino3dm.min.js`, `rhino/rhino3dm.wasm` | rhino3dm, for reading and writing `.3dm` files |
| `rhino/sw_link.py` | Sun & Wind Link, the Rhino 7 / 8 panel behind the live link |
| `link.js` | A marker the Rhino panel looks for, so it can serve this folder |

## Rhino

- **Files:** Import and Export take `.3dm` (also `.obj` and `.json`). Each layer becomes tower massing or site context; a model check then sorts the massing into floor plates, ground floor, façade, windows, roof, core and so on before anything is brought in.
- **Live link:** in Rhino run `rhino/sw_link.py` (RunPythonScript), press Start link, then Open the tool. The panel serves this folder on `http://127.0.0.1:8797/` and the workspace re-imports the model after every edit. In the Massing tab, "Live link to Rhino" connects by hand.

## Where the data stays

Projects are saved in the browser (local storage). Use Export to keep a project file, a `.3dm`, or the analysis report.

## Limits to know

- The checks outside sun, wind, rain and energy are simple rules; they point at what to look into and do not replace a consultant's study or a code review.
- The map covers only the blocks around the competition site and uses the base model's generic street labels.
- Published as a Claude artifact, the page cannot reach `127.0.0.1`, so the live link works only from a local copy.

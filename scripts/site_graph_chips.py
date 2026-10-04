"""Every public post's and projected page's chips from the notes graph -- the chips file the
listing-level render proof explains changed chips by (design ce17606b; build 096b6864).

    conda run -n cjm-transcript-correction-core python scripts/site_graph_chips.py <out.json> [<profile>]

Writes {source path without its extension: [chips]}: each public post's categories (one with no
facet carries []), and each page the build projects under the profile (default public) with
its planned categories. Then:

    python scripts/site_listing_items.py --compare <before.json> <after.json> <out.json>

reads a changed chip as explained when it is its post's from the graph."""

import asyncio
import json
import posixpath
import sys
from pathlib import Path

DB = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills/cjm-substrate/.cjm/notes/notes-graph.db"
ROOT = Path("/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills/christianjmills").resolve()


async def chips_map(profile: str) -> dict:
    from cjm_context_graph_projection import factlayer as F
    from cjm_context_graph_projection.categories import load_post_categories
    from cjm_context_graph_projection.judgeengine import public_posts
    from cjm_context_graph_projection.runtime import open_graph
    from cjm_context_graph_projection.site import redirect_plan
    from cjm_context_graph_projection.sitepages import page_plan
    async with open_graph(DB, readonly=True) as gx:
        got, pub = await load_post_categories(gx), await public_posts(gx)
        notes = await F.load_nodes(gx, list(pub))
        out = {}
        for n in pub:
            p = Path(str(F.prop(notes[n], "path"))).resolve()
            if p.is_relative_to(ROOT):
                out[posixpath.splitext(p.relative_to(ROOT).as_posix())[0]] = got["posts"].get(n, [])
        plan = await page_plan(gx, str(ROOT), profile, (await redirect_plan(gx))["pages"])
        for page in plan["pages"]:
            out[posixpath.splitext(page["source"])[0]] = page.get("categories") or []
        return {"chips": out, "errors": plan["errors"]}


if __name__ == "__main__":
    res = asyncio.run(chips_map(sys.argv[2] if len(sys.argv) > 2 else "public"))
    Path(sys.argv[1]).write_text(json.dumps(res["chips"], indent=1, ensure_ascii=False))
    print(f"{len(res['chips'])} entries; {len(res['errors'])} plan error(s)")

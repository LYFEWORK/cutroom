"""cap_remotion.py: a throwaway Remotion project for the tests that really render or typecheck the caption composition.

The composition lives in remotion/captions/ and is registered by the project's own Root.tsx (one import of captions/register.ts). These tests do not depend
on that Root: project() builds a tiny one in a temporary folder, copies remotion/captions/ into it (the bundler wants real files), and links the repo's
remotion/node_modules, so the tests run wherever the renderer is installed and skip (needs_remotion) where it is not.
"""
import os
import shutil

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
REMOTION = os.path.join(REPO, "remotion")
NODE_MODULES = os.path.join(REMOTION, "node_modules")
REMOTION_BIN = os.path.join(NODE_MODULES, ".bin", "remotion")
TSC_BIN = os.path.join(NODE_MODULES, ".bin", "tsc")

needs_remotion = pytest.mark.skipif(not os.path.exists(REMOTION_BIN), reason="Remotion is not installed (npm ci in remotion/)")
needs_tsc = pytest.mark.skipif(not os.path.exists(TSC_BIN), reason="TypeScript is not installed (npm ci in remotion/)")

ROOT_TSX = '''import React from "react";
import { Composition } from "remotion";
import { captions } from "./captions/register";

export const RemotionRoot: React.FC = () => (
  <>
    <Composition {...captions} />
  </>
);
'''
INDEX_TS = '''import { registerRoot } from "remotion";
import { RemotionRoot } from "./Root";

registerRoot(RemotionRoot);
'''
TSCONFIG = '''{"compilerOptions": {"target": "ES2022", "lib": ["dom", "dom.iterable", "es2022"], "module": "ESNext", "moduleResolution": "bundler", "jsx": "react-jsx", "strict": true, "noEmit": true, "skipLibCheck": true}, "include": ["*.ts", "*.tsx", "captions/*.ts", "captions/*.tsx"]}
'''
PACKAGE = '{"name": "captions-test-project", "version": "1.0.0", "private": true}\n'


def project(dest):
    """Build the project in `dest` (an empty or new folder) and return its path. The composition files are copied from remotion/captions/ now, so a change to
    them is seen by the next project() call. Skips the test (pytest.skip) when the renderer is not installed."""
    if not os.path.exists(REMOTION_BIN):
        pytest.skip("Remotion is not installed (npm ci in remotion/)")
    dest = str(dest)
    os.makedirs(dest, exist_ok=True)
    shutil.copytree(os.path.join(REMOTION, "captions"), os.path.join(dest, "captions"), dirs_exist_ok=True)
    for name, text in (("Root.tsx", ROOT_TSX), ("index.ts", INDEX_TS), ("tsconfig.json", TSCONFIG), ("package.json", PACKAGE)):
        with open(os.path.join(dest, name), "w") as f:
            f.write(text)
    link = os.path.join(dest, "node_modules")
    if not os.path.lexists(link):
        os.symlink(os.path.realpath(NODE_MODULES), link)
    return dest

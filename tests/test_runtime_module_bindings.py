"""Keep compiled runtime tests portable without weakening symbol selection."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_module_bindings as binding


class RuntimeModuleBindingTests(unittest.TestCase):
    def fixture(self, generation):
        base = "apps/server/dist/"
        entry = base + "bin.mjs"
        runtime = base + f"routing-{generation}.mjs"
        implementations = base + f"dependencies-{generation}.mjs"
        by_source = {}
        expected = {}
        for index, (name, (section, symbol)) in enumerate(binding.SYMBOLS.items()):
            renamed = f"{symbol}${generation + index}"
            expected[name] = renamed
            by_source.setdefault(section, []).append(f"const {renamed} = {index};\n")
        definitions = "".join(
            f"// ../../{section}\n" + "".join(lines)
            for section, lines in by_source.items()
        )
        source = (
            f'import "./dependencies-{generation}.mjs";\n'
            "function dispatch(command) {\n"
            "\t\tconst noticeKind = command.notification?.source.kind;\n"
            "\t\tif (noticeKind === 'pull-request-watch') dispatchMode = 'goal';\n"
            "\t\tconst notification = command.manualContinuationOfRunId === void 0 ? null : command.notification;\n"
            "}\n"
        )
        modules = {
            entry: f'import "./routing-{generation}.mjs";\n',
            runtime: source,
            implementations: definitions,
            # A stale chunk with identical declarations must never participate.
            base + "unused-old-chunk.mjs": definitions,
        }
        patch = binding.load_patcher()
        return {
            "sources": modules, "active_paths": patch.active_module_paths(modules),
            "runtime": runtime, "server": implementations,
        }, expected

    def test_renamed_chunks_and_renumbered_symbols_generate_portable_imports(self):
        for generation in (17, 640):
            with self.subTest(generation=generation):
                resolved, expected = self.fixture(generation)
                found = binding.resolve_symbols(resolved["sources"], resolved["active_paths"])
                self.assertEqual({name: item["symbol"] for name, item in found.items()}, expected)
                replacements, generated = binding.build_instrumentation(resolved, "/copied/app.asar")
                self.assertEqual(set(generated), {
                    "preview-test-bindings.mjs", "pr-watch-test-bindings.mjs", "antigravity-test-bindings.mjs",
                })
                for path, source in replacements.items():
                    self.assertTrue(source.startswith(resolved["sources"][path].encode()))
                self.assertNotIn("unused-old-chunk", "".join(generated.values()))
                for name, item in found.items():
                    self.assertIn(
                        f'export {{{expected[name]} as {item["export"]}}};',
                        replacements[item["path"]].decode(),
                    )
                routing = replacements[resolved["runtime"]].decode()
                self.assertIn(f'from "./dependencies-{generation}.mjs";', routing)
                self.assertIn("__t3FlatpakTest_policyGen(function*", routing)
                self.assertIn("if (noticeKind === 'pull-request-watch') dispatchMode = 'goal';", routing)

    def test_duplicate_live_definition_fails_closed(self):
        resolved, _ = self.fixture(80)
        path = "apps/server/dist/duplicate.mjs"
        resolved["sources"][path] = "// " + binding.EFFECT + "\nconst runPromise$900 = null;\n"
        resolved["active_paths"] = resolved["active_paths"] | {path}
        with self.assertRaisesRegex(ValueError, "exactly one live.*runPromise; found 2"):
            binding.build_instrumentation(resolved, "/copied/app.asar")

    def test_quoted_declaration_lookalikes_are_ignored(self):
        resolved, expected = self.fixture(80)
        path = resolved["server"]
        marker = "// ../../" + binding.EFFECT + "\n"
        resolved["sources"][path] = resolved["sources"][path].replace(
            marker, marker + "const example = `\nconst runPromise$999 = null;\n`;\n"
        )
        found = binding.resolve_symbols(resolved["sources"], resolved["active_paths"])
        self.assertEqual(found["runPromise"]["symbol"], expected["runPromise"])

    def test_matching_name_in_wrong_original_module_is_rejected(self):
        resolved, _ = self.fixture(80)
        path = resolved["server"]
        resolved["sources"][path] = resolved["sources"][path].replace(
            "// ../../" + binding.EFFECT + "\n", "// ../../node_modules/unrelated/Effect.js\n"
        )
        with self.assertRaisesRegex(ValueError, "exactly one live.*runPromise; found 0"):
            binding.build_instrumentation(resolved, "/copied/app.asar")

    def test_ambiguous_policy_and_second_instrumentation_are_rejected(self):
        resolved, _ = self.fixture(80)
        replacements, _ = binding.build_instrumentation(resolved, "/copied/app.asar")
        resolved["sources"].update({path: source.decode() for path, source in replacements.items()})
        with self.assertRaisesRegex(ValueError, "already instrumented"):
            binding.build_instrumentation(resolved, "/copied/app.asar")
        resolved, _ = self.fixture(81)
        resolved["sources"][resolved["runtime"]] *= 2
        with self.assertRaisesRegex(ValueError, "exactly one notification routing block"):
            binding.build_instrumentation(resolved, "/copied/app.asar")


if __name__ == "__main__":
    unittest.main()

"""
Code Structure Analyzer for Learning System

Analyzes project structure, dependencies, and code quality metrics.
Helps AI understand codebase architecture for better decision-making.
"""

import os
import ast
import re
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Optional, Any, Tuple
from collections import defaultdict, Counter
import logging

logger = logging.getLogger('shadowops.code_analyzer')


class CodeAnalyzer:
    """
    Analyzes Python codebase structure and quality

    Features:
    - Project structure mapping (modules, files, classes, functions)
    - Dependency graph (imports, critical modules)
    - Code quality metrics (LOC, complexity, documentation)
    - Entry-point detection
    - Bottleneck identification
    """

    def __init__(self, project_path: str):
        """
        Args:
            project_path: Root path of project to analyze
        """
        self.project_path = Path(project_path)
        self.source_dir = self.project_path / 'src'

        # Cache
        self.structure_cache: Optional[Dict[str, Any]] = None
        self.dependency_cache: Optional[Dict[str, Any]] = None
        self.metrics_cache: Optional[Dict[str, Any]] = None

        # Stats
        self.total_files = 0
        self.total_lines = 0
        self.total_functions = 0
        self.total_classes = 0
        self.total_js_ts_functions = 0
        self.total_js_ts_classes = 0
        self.total_js_ts_exports = 0

    def analyze_all(self, force_reload: bool = False) -> Dict[str, Any]:
        """
        Run complete analysis

        Args:
            force_reload: Force cache refresh

        Returns:
            Dict with all analysis results
        """
        if not force_reload and all([
            self.structure_cache,
            self.dependency_cache,
            self.metrics_cache
        ]):
            logger.debug("Using cached analysis results")
            return self._build_complete_result()

        logger.info("🔍 Starting code structure analysis...")

        # Run all analyses
        self.structure_cache = self.analyze_structure()
        self.dependency_cache = self.build_dependency_graph()
        self.metrics_cache = self.calculate_metrics()

        logger.info(f"✅ Analysis complete: {self.total_files} files, {self.total_lines} LOC")

        return self._build_complete_result()

    def _build_complete_result(self) -> Dict[str, Any]:
        """Combine all cached results"""
        return {
            'structure': self.structure_cache,
            'dependencies': self.dependency_cache,
            'metrics': self.metrics_cache,
            'summary': {
                'total_files': self.total_files,
                'total_lines': self.total_lines,
                'total_functions': self.total_functions,
                'total_classes': self.total_classes
            }
        }

    def _get_source_dirs(self) -> List[Path]:
        """Return list of source directories to inspect (with sensible fallbacks)."""
        candidates = [
            self.source_dir,
            self.project_path / 'backend' / 'src',
            self.project_path / 'frontend' / 'src'
        ]

        seen = []
        for c in candidates:
            if c.exists() and c.is_dir():
                seen.append(c)

        # If nothing matches, fall back to project root to at least count files
        return seen or [self.project_path]

    def _iter_source_files(self, source_dirs: List[Path]):
        """Yield source files across supported extensions."""
        extensions = ['*.py', '*.ts', '*.tsx', '*.js', '*.jsx']
        for base in source_dirs:
            for ext in extensions:
                for path in base.rglob(ext):
                    if '__pycache__' in str(path):
                        continue
                    yield path

    def analyze_structure(self) -> Dict[str, Any]:
        """
        Analyze project structure

        Returns:
            Dict with modules, files, classes, functions
        """
        source_dirs = self._get_source_dirs()

        structure = {
            'modules': {},
            'entry_points': []
        }

        # Walk through supported source files
        for src_file in self._iter_source_files(source_dirs):
            # Derive module name relative to closest source dir
            rel_base = next((base for base in source_dirs if src_file.is_relative_to(base)), source_dirs[0])
            relative_path = src_file.relative_to(rel_base)
            module_name = str(relative_path.with_suffix('')).replace(os.sep, '.')

            try:
                file_info = self._analyze_file(src_file)
                # Test heuristics
                parts_lower = [p.lower() for p in relative_path.parts]
                is_test = any('test' in p or 'spec' in p for p in parts_lower)
                file_info['is_test'] = is_test
                structure['modules'][module_name] = file_info

                # Detect entry points (files with if __name__ == '__main__')
                if file_info.get('is_entry_point'):
                    structure['entry_points'].append(module_name)

                self.total_files += 1

            except Exception as e:
                logger.debug(f"Could not analyze {src_file}: {e}")

        return structure

    def _analyze_file(self, file_path: Path) -> Dict[str, Any]:
        """
        Analyze single Python file

        Args:
            file_path: Path to Python file

        Returns:
            Dict with file information
        """
        info = {
            'path': str(file_path),
            'classes': [],
            'functions': [],
            'imports': [],
            'lines': 0,
            'js_functions': [],
            'js_classes': [],
            'exports': [],
            'has_docstring': False,
            'is_entry_point': False
        }

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()

            # Count lines
            info['lines'] = len(source.splitlines())
            self.total_lines += info['lines']

            # Python: deep analysis via AST
            if file_path.suffix == '.py':
                tree = ast.parse(source, filename=str(file_path))

                # Check for module docstring
                info['has_docstring'] = ast.get_docstring(tree) is not None

                # Walk AST
                for node in ast.walk(tree):
                    # Classes
                    if isinstance(node, ast.ClassDef):
                        class_info = {
                            'name': node.name,
                            'methods': [],
                            'has_docstring': ast.get_docstring(node) is not None,
                            'line': node.lineno
                        }

                        # Extract methods
                        for item in node.body:
                            if isinstance(item, ast.FunctionDef):
                                class_info['methods'].append({
                                    'name': item.name,
                                    'has_docstring': ast.get_docstring(item) is not None,
                                    'line': item.lineno
                                })

                        info['classes'].append(class_info)
                        self.total_classes += 1

                    # Functions (top-level only)
                    elif isinstance(node, ast.FunctionDef) and node.col_offset == 0:
                        info['functions'].append({
                            'name': node.name,
                            'has_docstring': ast.get_docstring(node) is not None,
                            'line': node.lineno,
                            'is_async': isinstance(node, ast.AsyncFunctionDef)
                        })
                        self.total_functions += 1

                    # Imports
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            info['imports'].append({
                                'type': 'import',
                                'module': alias.name,
                                'alias': alias.asname
                            })

                    elif isinstance(node, ast.ImportFrom):
                        module = node.module or ''
                        for alias in node.names:
                            info['imports'].append({
                                'type': 'from',
                                'module': module,
                                'name': alias.name,
                                'alias': alias.asname
                            })

                    # Entry point detection
                    elif isinstance(node, ast.If):
                        if self._is_main_check(node):
                            info['is_entry_point'] = True

            else:
                # Non-Python: try AST via esprima helper script; fallback to regex heuristics
                info['has_docstring'] = False
                info['functions'] = []
                info['classes'] = []

                esprima_bin = Path(__file__).parent.parent.parent / 'scripts' / 'parse-js-ts.js'
                if esprima_bin.exists():
                    try:
                        result = subprocess.run(
                            ['node', str(esprima_bin), str(file_path)],
                            capture_output=True,
                            text=True,
                            timeout=10
                        )
                        if result.returncode == 0:
                            summaries = json.loads(result.stdout)
                            if summaries:
                                summary = summaries[0]
                                info['imports'].extend([{'type': 'import', 'module': m} for m in summary.get('imports', [])])
                                info['js_functions'].extend(summary.get('functions', []))
                                info['js_classes'].extend(summary.get('classes', []))
                                info['exports'].extend(summary.get('exports', []))
                        else:
                            logger.debug(f"esprima parser failed for {file_path}: {result.stderr}")
                    except Exception as e:
                        logger.debug(f"esprima parser error for {file_path}: {e}")

                # Fallback regex heuristics if nothing parsed
                if not info['js_functions'] and not info['js_classes'] and not info['imports']:
                    js_func_pattern = re.compile(r'\bfunction\s+([A-Za-z0-9_]+)')
                    js_arrow_pattern = re.compile(r'\b(const|let|var)\s+([A-Za-z0-9_]+)\s*=\s*(async\s+)?\(?.*?=>')
                    js_class_pattern = re.compile(r'\bclass\s+([A-Za-z0-9_]+)')
                    js_export_pattern = re.compile(r'\bexport\s+(default\s+)?(function|class|const|let|var)\s+([A-Za-z0-9_]+)?')

                    for line in source.splitlines():
                        stripped = line.strip()

                        if stripped.startswith('import ') or stripped.startswith('require('):
                            parts = stripped.replace('import', '').replace('require', '').split()
                            module_name = parts[0] if parts else ''
                            info['imports'].append({'type': 'import', 'module': module_name})

                        # Functions
                        m_func = js_func_pattern.search(stripped)
                        if m_func:
                            info['js_functions'].append(m_func.group(1))

                        m_arrow = js_arrow_pattern.search(stripped)
                        if m_arrow:
                            info['js_functions'].append(m_arrow.group(2))

                        # Classes
                        m_class = js_class_pattern.search(stripped)
                        if m_class:
                            info['js_classes'].append(m_class.group(1))

                        # Exports
                        m_export = js_export_pattern.search(stripped)
                        if m_export:
                            exported = m_export.group(3) or 'default'
                            info['exports'].append(exported)

        except Exception as e:
            logger.debug(f"Error parsing {file_path}: {e}")

        return info

    def _is_main_check(self, node: ast.If) -> bool:
        """Check if node is 'if __name__ == "__main__"'"""
        try:
            if isinstance(node.test, ast.Compare):
                left = node.test.left
                if isinstance(left, ast.Name) and left.id == '__name__':
                    if node.test.ops and isinstance(node.test.ops[0], ast.Eq):
                        comparator = node.test.comparators[0]
                        if isinstance(comparator, ast.Constant):
                            return comparator.value == '__main__'
        except:
            pass
        return False

    def build_dependency_graph(self) -> Dict[str, Any]:
        """
        Build import dependency graph

        Returns:
            Dict with dependency information
        """
        if not self.structure_cache:
            self.analyze_structure()

        graph = {
            'imports': {},  # {module: [imported_modules]}
            'imported_by': defaultdict(list),  # {module: [modules_that_import_it]}
            'critical_modules': [],  # Most imported modules
            'isolated_modules': [],  # No imports
            'external_dependencies': Counter(),  # External libs
            'cycles': []  # Import cycles (detected later)
        }

        # Build graph
        for module_name, module_info in self.structure_cache['modules'].items():
            imports = []

            for imp in module_info.get('imports', []):
                imported_module = imp.get('module', '')

                # Check if internal or external
                if self._is_internal_module(imported_module):
                    imports.append(imported_module)
                    graph['imported_by'][imported_module].append(module_name)
                else:
                    # External dependency
                    root_module = imported_module.split('.')[0]
                    graph['external_dependencies'][root_module] += 1

            graph['imports'][module_name] = imports

            if not imports:
                graph['isolated_modules'].append(module_name)

        # Find critical modules (most imported)
        import_counts = Counter()
        for module, importers in graph['imported_by'].items():
            import_counts[module] = len(importers)

        graph['critical_modules'] = [
            {'module': mod, 'imported_by_count': count}
            for mod, count in import_counts.most_common(10)
        ]

        # Detect cycles
        graph['cycles'] = self._find_cycles(graph['imports'])

        return dict(graph)

    def _is_internal_module(self, module_name: str) -> bool:
        """Check if module is internal to project"""
        # Simple heuristic: check if module starts with common internal prefixes
        internal_prefixes = ['src', 'integrations', 'utils', 'handlers', 'services', 'commands']

        # Relative imports are considered internal
        if module_name.startswith('.'):
            return True

        for prefix in internal_prefixes:
            if module_name.startswith(prefix):
                return True

        # Check if it exists in structure
        if self.structure_cache and module_name in self.structure_cache['modules']:
            return True

        return False

    def _find_cycles(self, import_graph: Dict[str, List[str]]) -> List[List[str]]:
        """Detect simple import cycles."""
        cycles = []
        visiting = set()
        visited = set()
        stack = []

        def dfs(node: str):
            if node in visiting:
                # cycle found
                cycle_start_index = stack.index(node) if node in stack else 0
                cycles.append(stack[cycle_start_index:] + [node])
                return
            if node in visited:
                return
            visiting.add(node)
            stack.append(node)
            for neigh in import_graph.get(node, []):
                dfs(neigh)
                if len(cycles) >= 5:  # cap noise
                    break
            stack.pop()
            visiting.remove(node)
            visited.add(node)

        for module in import_graph.keys():
            if module not in visited:
                dfs(module)
                if len(cycles) >= 5:
                    break
        # Deduplicate cycles by string repr
        seen = set()
        uniq = []
        for c in cycles:
            sig = '>'.join(c)
            if sig not in seen:
                seen.add(sig)
                uniq.append(c)
        return uniq

    def calculate_metrics(self) -> Dict[str, Any]:
        """
        Calculate code quality metrics

        Returns:
            Dict with quality metrics
        """
        if not self.structure_cache:
            self.analyze_structure()

        metrics = {
            'total_lines': self.total_lines,
            'total_files': self.total_files,
            'total_classes': self.total_classes,
            'total_functions': self.total_functions,
            'js_ts': {
                'functions': 0,
                'classes': 0,
                'exports': 0
            },
            'documentation': {
                'files_with_docstrings': 0,
                'classes_with_docstrings': 0,
                'functions_with_docstrings': 0,
                'documentation_coverage': 0.0
            },
            'complexity': {
                'largest_files': [],  # Files with most LOC
                'largest_classes': [],  # Classes with most methods
                'avg_lines_per_file': 0.0,
                'avg_functions_per_file': 0.0
            },
            'testing': {
                'test_files': 0,
                'coverage': None,
                'frameworks': []
            }
        }

        # Documentation metrics
        total_documented = 0
        total_documentable = 0

        file_sizes = []
        class_sizes = []

        for module_name, module_info in self.structure_cache['modules'].items():
            # File docstrings
            if module_info.get('has_docstring'):
                metrics['documentation']['files_with_docstrings'] += 1
                total_documented += 1
            total_documentable += 1

            # File size
            file_sizes.append({
                'module': module_name,
                'lines': module_info.get('lines', 0)
            })

            # JS/TS metrics
            metrics['js_ts']['functions'] += len(module_info.get('js_functions', []))
            metrics['js_ts']['classes'] += len(module_info.get('js_classes', []))
            metrics['js_ts']['exports'] += len(module_info.get('exports', []))

            # Tests
            if module_info.get('is_test'):
                metrics['testing']['test_files'] += 1

            # Classes
            for cls in module_info.get('classes', []):
                if cls.get('has_docstring'):
                    metrics['documentation']['classes_with_docstrings'] += 1
                    total_documented += 1
                total_documentable += 1

                class_sizes.append({
                    'module': module_name,
                    'class': cls['name'],
                    'methods': len(cls.get('methods', []))
                })

                # Methods
                for method in cls.get('methods', []):
                    if method.get('has_docstring'):
                        metrics['documentation']['functions_with_docstrings'] += 1
                        total_documented += 1
                    total_documentable += 1

            # Functions
            for func in module_info.get('functions', []):
                if func.get('has_docstring'):
                    metrics['documentation']['functions_with_docstrings'] += 1
                    total_documented += 1
                total_documentable += 1

        # Coverage + frameworks detection
        metrics['testing']['coverage'] = self._detect_coverage()
        metrics['testing']['frameworks'] = self._detect_test_frameworks()

        # Calculate coverage
        if total_documentable > 0:
            metrics['documentation']['documentation_coverage'] = round(
                (total_documented / total_documentable) * 100, 1
            )

        # Complexity metrics
        if self.total_files > 0:
            metrics['complexity']['avg_lines_per_file'] = round(
                self.total_lines / self.total_files, 1
            )
            metrics['complexity']['avg_functions_per_file'] = round(
                self.total_functions / self.total_files, 1
            )

        # Largest files
        metrics['complexity']['largest_files'] = sorted(
            file_sizes, key=lambda x: x['lines'], reverse=True
        )[:5]

        # Classes with most methods
        metrics['complexity']['largest_classes'] = sorted(
            class_sizes, key=lambda x: x['methods'], reverse=True
        )[:5]

        return metrics

    def _detect_coverage(self) -> Optional[float]:
        """Try to read coverage from common report files (coverage.xml, lcov.info)."""
        # coverage.xml
        cov_xml = list(self.project_path.glob('**/coverage.xml'))
        for path in cov_xml:
            try:
                import xml.etree.ElementTree as ET
                tree = ET.parse(path)
                root = tree.getroot()
                if 'line-rate' in root.attrib:
                    return round(float(root.attrib['line-rate']) * 100, 1)
            except Exception:
                continue

        # lcov.info
        lcov_files = list(self.project_path.glob('**/lcov.info'))
        for path in lcov_files:
            try:
                total_lines = 0
                covered_lines = 0
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        if line.startswith('LF:'):
                            total_lines += int(line.split(':')[1].strip())
                        elif line.startswith('LH:'):
                            covered_lines += int(line.split(':')[1].strip())
                if total_lines > 0:
                    return round((covered_lines / total_lines) * 100, 1)
            except Exception:
                continue
        return None

    def _detect_test_frameworks(self) -> List[str]:
        """Detect likely test frameworks based on common config files."""
        frameworks = []
        candidates = {
            'pytest': ['pytest.ini', 'pyproject.toml'],
            'jest': ['jest.config.js', 'jest.config.ts', 'jest.config.mjs'],
            'vitest': ['vitest.config.ts', 'vitest.config.js'],
            'unittest': []  # implicit via Python stdlib
        }

        for name, files in candidates.items():
            for fname in files:
                if (self.project_path / fname).exists():
                    frameworks.append(name)
                    break

        # package.json scripts
        pkg_json = self.project_path / 'package.json'
        if pkg_json.exists():
            try:
                import json
                data = json.loads(pkg_json.read_text(encoding='utf-8'))
                scripts = data.get('scripts', {})
                for key in scripts.keys():
                    if 'test' in key.lower():
                        if 'jest' in scripts[key]:
                            frameworks.append('jest')
                        elif 'vitest' in scripts[key]:
                            frameworks.append('vitest')
                        else:
                            frameworks.append('npm-test')
                        break
            except Exception:
                pass

        # deduplicate
        return sorted(set(frameworks))

    def generate_context_for_ai(self, focus_area: Optional[str] = None) -> str:
        """
        Generate AI context from code analysis

        Args:
            focus_area: Optional focus ('security', 'performance', 'structure')

        Returns:
            Formatted context string for AI
        """
        if not all([self.structure_cache, self.dependency_cache, self.metrics_cache]):
            self.analyze_all()

        context_parts = []

        # === PROJECT OVERVIEW ===
        context_parts.append("# CODE STRUCTURE INSIGHTS\n")
        context_parts.append(f"**Project Size**: {self.total_files} files, {self.total_lines:,} lines of code")
        context_parts.append(f"**Components**: {self.total_classes} classes, {self.total_functions} functions")
        context_parts.append(f"**Documentation**: {self.metrics_cache['documentation']['documentation_coverage']}% coverage\n")

        # === ENTRY POINTS ===
        entry_points = self.structure_cache.get('entry_points', [])
        if entry_points:
            context_parts.append("## Entry Points")
            for ep in entry_points[:5]:
                context_parts.append(f"- `{ep}`")
            context_parts.append("")

        # === CRITICAL MODULES ===
        critical = self.dependency_cache.get('critical_modules', [])
        if critical:
            context_parts.append("## Critical Modules (Most Dependencies)")
            for mod in critical[:5]:
                context_parts.append(
                    f"- `{mod['module']}` - imported by {mod['imported_by_count']} modules"
                )
            context_parts.append("")

        # === COMPLEXITY HOTSPOTS ===
        largest_files = self.metrics_cache['complexity']['largest_files']
        if largest_files:
            context_parts.append("## Complexity Hotspots")
            for f in largest_files[:3]:
                context_parts.append(f"- `{f['module']}` - {f['lines']} lines")
            context_parts.append("")

        # === EXTERNAL DEPENDENCIES ===
        ext_deps = self.dependency_cache.get('external_dependencies', Counter())
        if ext_deps:
            context_parts.append("## Key External Dependencies")
            for dep, count in ext_deps.most_common(8):
                context_parts.append(f"- `{dep}` (used {count}x)")
            context_parts.append("")

        # === FOCUS AREA SPECIFIC ===
        if focus_area == 'security':
            context_parts.append("## Security-Relevant Modules")
            for module_name in self.structure_cache['modules'].keys():
                if any(keyword in module_name.lower() for keyword in [
                    'security', 'auth', 'permission', 'safe', 'check', 'validate'
                ]):
                    context_parts.append(f"- `{module_name}`")
            context_parts.append("")

        elif focus_area == 'performance':
            context_parts.append("## Performance-Critical Areas")
            context_parts.append(f"Average file size: {self.metrics_cache['complexity']['avg_lines_per_file']} LOC")
            context_parts.append(f"Largest file: {largest_files[0]['lines']} LOC" if largest_files else "")
            context_parts.append("")

        return '\n'.join(context_parts)

    def get_module_info(self, module_name: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed info about specific module

        Args:
            module_name: Name of module

        Returns:
            Module info dict or None
        """
        if not self.structure_cache:
            self.analyze_structure()

        return self.structure_cache['modules'].get(module_name)

    def find_modules_by_keyword(self, keyword: str) -> List[str]:
        """
        Find modules containing keyword

        Args:
            keyword: Search term

        Returns:
            List of matching module names
        """
        if not self.structure_cache:
            self.analyze_structure()

        keyword_lower = keyword.lower()
        matches = []

        for module_name in self.structure_cache['modules'].keys():
            if keyword_lower in module_name.lower():
                matches.append(module_name)

        return matches

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get complete statistics

        Returns:
            Dict with all statistics
        """
        if not all([self.structure_cache, self.dependency_cache, self.metrics_cache]):
            self.analyze_all()

        return {
            'files': self.total_files,
            'lines': self.total_lines,
            'classes': self.total_classes,
            'functions': self.total_functions,
            'documentation_coverage': self.metrics_cache['documentation']['documentation_coverage'],
            'entry_points': len(self.structure_cache.get('entry_points', [])),
            'critical_modules': len(self.dependency_cache.get('critical_modules', [])),
            'external_dependencies': len(self.dependency_cache.get('external_dependencies', {}))
        }


if __name__ == '__main__':
    # Quick test
    import sys

    logging.basicConfig(level=logging.INFO)

    project_path = Path(__file__).parent.parent.parent
    print(f"Analyzing project: {project_path}\n")

    analyzer = CodeAnalyzer(str(project_path))

    # Run analysis
    results = analyzer.analyze_all()

    # Print statistics
    stats = analyzer.get_statistics()
    print("=== PROJECT STATISTICS ===")
    print(f"Files: {stats['files']}")
    print(f"Lines of Code: {stats['lines']:,}")
    print(f"Classes: {stats['classes']}")
    print(f"Functions: {stats['functions']}")
    print(f"Documentation: {stats['documentation_coverage']}%")
    print(f"Entry Points: {stats['entry_points']}")
    print(f"External Dependencies: {stats['external_dependencies']}")

    print("\n=== AI CONTEXT ===")
    print(analyzer.generate_context_for_ai())

"""
Code Structure Analyzer for Learning System

Analyzes project structure, dependencies, and code quality metrics.
Helps AI understand codebase architecture for better decision-making.
"""

import os
import ast
import re
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

    def analyze_structure(self) -> Dict[str, Any]:
        """
        Analyze project structure

        Returns:
            Dict with modules, files, classes, functions
        """
        if not self.source_dir.exists():
            logger.warning(f"Source directory not found: {self.source_dir}")
            return {'modules': {}, 'entry_points': []}

        structure = {
            'modules': {},
            'entry_points': []
        }

        # Walk through all Python files
        for py_file in self.source_dir.rglob('*.py'):
            if '__pycache__' in str(py_file):
                continue

            relative_path = py_file.relative_to(self.source_dir)
            module_name = str(relative_path.with_suffix('')).replace(os.sep, '.')

            try:
                file_info = self._analyze_file(py_file)
                structure['modules'][module_name] = file_info

                # Detect entry points (files with if __name__ == '__main__')
                if file_info.get('is_entry_point'):
                    structure['entry_points'].append(module_name)

                self.total_files += 1

            except Exception as e:
                logger.debug(f"Could not analyze {py_file}: {e}")

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
            'has_docstring': False,
            'is_entry_point': False
        }

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()

            # Count lines
            info['lines'] = len(source.splitlines())
            self.total_lines += info['lines']

            # Parse AST
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
            'external_dependencies': Counter()  # External libs
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

        return dict(graph)

    def _is_internal_module(self, module_name: str) -> bool:
        """Check if module is internal to project"""
        # Simple heuristic: check if module starts with common internal prefixes
        internal_prefixes = ['src', 'integrations', 'utils', 'handlers', 'services', 'commands']

        for prefix in internal_prefixes:
            if module_name.startswith(prefix):
                return True

        # Check if it exists in structure
        if self.structure_cache and module_name in self.structure_cache['modules']:
            return True

        return False

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

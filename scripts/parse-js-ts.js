#!/usr/bin/env node
/**
 * Lightweight JS/TS parser for ShadowOps code analysis.
 * Uses esprima for JS/TS (tolerant) parsing and outputs a compact JSON summary.
 */

const fs = require('fs');
const path = require('path');
const esprima = require('esprima');

function parseFile(filePath) {
  const source = fs.readFileSync(filePath, 'utf8');
  const isTS = filePath.endsWith('.ts') || filePath.endsWith('.tsx');

  const ast = esprima.parseModule(source, {
    jsx: filePath.endsWith('.tsx') || filePath.endsWith('.jsx'),
    tolerant: true,
    tokens: false,
    loc: false,
  });

  const summary = {
    path: filePath,
    exports: [],
    imports: [],
    functions: [],
    classes: [],
  };

  function addExport(name) {
    if (name && !summary.exports.includes(name)) summary.exports.push(name);
  }

  esprima.traverse = function traverse(node, visitor) {
    visitor(node);
    for (const key in node) {
      if (node.hasOwnProperty(key)) {
        const child = node[key];
        if (Array.isArray(child)) {
          child.forEach((c) => c && typeof c.type === 'string' && traverse(c, visitor));
        } else if (child && typeof child.type === 'string') {
          traverse(child, visitor);
        }
      }
    }
  };

  esprima.traverse(ast, (node) => {
    switch (node.type) {
      case 'ImportDeclaration':
        if (node.source && node.source.value) summary.imports.push(node.source.value);
        break;
      case 'FunctionDeclaration':
        if (node.id && node.id.name) summary.functions.push(node.id.name);
        break;
      case 'VariableDeclaration':
        node.declarations.forEach((decl) => {
          if (
            decl.init &&
            (decl.init.type === 'ArrowFunctionExpression' || decl.init.type === 'FunctionExpression') &&
            decl.id &&
            decl.id.name
          ) {
            summary.functions.push(decl.id.name);
          }
        });
        break;
      case 'ClassDeclaration':
        if (node.id && node.id.name) summary.classes.push(node.id.name);
        break;
      case 'ExportNamedDeclaration':
        if (node.declaration) {
          const decl = node.declaration;
          if (decl.id && decl.id.name) addExport(decl.id.name);
          if (decl.declarations) {
            decl.declarations.forEach((d) => d.id && addExport(d.id.name));
          }
        }
        if (node.specifiers) {
          node.specifiers.forEach((s) => s.exported && addExport(s.exported.name));
        }
        break;
      case 'ExportDefaultDeclaration':
        addExport('default');
        if (node.declaration && node.declaration.id && node.declaration.id.name) {
          addExport(node.declaration.id.name);
        }
        break;
      default:
        break;
    }
  });

  return summary;
}

function walkDir(dir, exts) {
  const results = [];
  for (const entry of fs.readdirSync(dir)) {
    const full = path.join(dir, entry);
    const stat = fs.statSync(full);
    if (stat.isDirectory()) {
      results.push(...walkDir(full, exts));
    } else {
      if (exts.some((ext) => full.endsWith(ext))) {
        results.push(full);
      }
    }
  }
  return results;
}

function main() {
  const target = process.argv[2];
  if (!target) {
    console.error('Usage: node parse-js-ts.js <file-or-dir>');
    process.exit(1);
  }
  const stats = fs.statSync(target);
  const files = stats.isDirectory()
    ? walkDir(target, ['.js', '.jsx', '.ts', '.tsx'])
    : [target];

  const summaries = files.map((f) => parseFile(f));
  console.log(JSON.stringify(summaries));
}

main();

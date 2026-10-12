// Decode only the shipped bundle's string table, never execute application code.
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');
const parser = require(process.env.BABEL_PARSER || '@babel/parser');
const sourcePath = process.argv[2];
const binary = fs.readFileSync(sourcePath);
const marker = Buffer.from("function _0x4917(");
const start = binary.indexOf(marker);
if (start < 0) throw new Error('Expected bundle marker not found');
const end = binary.indexOf(0, start);
const source = binary.subarray(start, end).toString('utf8');
const ast = parser.parse(source);
const bootstrap = ast.program.body[2].expression.expressions[0];
const context = vm.createContext(Object.create(null), {codeGeneration: {strings: false, wasm: false}});
vm.runInContext(source.slice(0, ast.program.body[1].end) + '(' + source.slice(bootstrap.start, bootstrap.end) + ');', context, {timeout: 2000});
const lookup = context._0x4917;
const replacements = [];
const aliases = new Set(['_0x4917']);
function collect(node) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'VariableDeclarator' && node.id.type === 'Identifier' && node.init?.type === 'Identifier' && aliases.has(node.init.name)) aliases.add(node.id.name);
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(collect);
    else if (value && typeof value === 'object') collect(value);
  }
}
let count;
do { count=aliases.size; collect(ast); } while (aliases.size!==count);
function visit(node) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'CallExpression' && node.callee.type === 'Identifier' && aliases.has(node.callee.name) && node.arguments.length === 1 && node.arguments[0].type === 'NumericLiteral') {
    const value = lookup(node.arguments[0].value);
    if (typeof value === 'string') replacements.push([node.start, node.end, JSON.stringify(value)]);
  }
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(visit);
    else if (value && typeof value === 'object') visit(value);
  }
}
// Replace only aliases established by literal identifier assignments.
visit(ast);
const pieces = [];
let cursor = 0;
for (const [a,b,value] of replacements.sort((a,b)=>a[0]-b[0])) {
  pieces.push(source.slice(cursor,a),value); cursor=b;
}
pieces.push(source.slice(cursor));
const decoded = pieces.join('');
fs.mkdirSync('verification/reverse', {recursive:true});
fs.writeFileSync('verification/reverse/bundle.decoded.js', decoded);
fs.writeFileSync('verification/reverse/artifact.json', JSON.stringify({sourcePath, sha256:crypto.createHash('sha256').update(binary).digest('hex'), bundleByteRange:[start,end], bundleSha256:crypto.createHash('sha256').update(binary.subarray(start,end)).digest('hex'), replacements:replacements.length, method:'Static extraction and isolated string table initialization; application not executed'},null,2));
console.log(JSON.stringify({start,end,replacements:replacements.length}));

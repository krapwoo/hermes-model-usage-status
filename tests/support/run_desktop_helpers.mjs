import fs from 'node:fs'
import vm from 'node:vm'

const pluginPath = process.argv[2]
const context = vm.createContext({
  console, Date, Intl, JSON, Math, Number, Promise,
  setTimeout, clearTimeout
})
const source = fs.readFileSync(pluginPath, 'utf8')
const plugin = new vm.SourceTextModule(source, { context, identifier: pluginPath })
const values = {
  '@hermes/plugin-sdk': {
    cn: (...parts) => parts.filter(Boolean).join(' '),
    Codicon: () => null,
    DropdownMenuItem: () => null,
    queryClient: { invalidateQueries() {}, setQueryData() {} },
    STATUSBAR_AREAS: { right: 'right' },
    useQuery: () => ({ data: null, isLoading: false })
  },
  react: { useState: value => [value, () => {}] },
  'react/jsx-runtime': {
    jsx: (type, props, key) => ({ type, props, key }),
    jsxs: (type, props, key) => ({ type, props, key })
  }
}

await plugin.link(async specifier => {
  const exports = values[specifier]
  if (!exports) throw new Error(`Unexpected import: ${specifier}`)
  let dependency
  dependency = new vm.SyntheticModule(Object.keys(exports), () => {
    for (const [name, value] of Object.entries(exports)) dependency.setExport(name, value)
  }, { context, identifier: specifier })
  return dependency
})
await plugin.evaluate()

let serialized = ''
for await (const chunk of process.stdin) serialized += chunk
const request = JSON.parse(serialized)
const helper = plugin.namespace[request.function]
if (typeof helper !== 'function') throw new Error(`Unknown helper: ${request.function}`)
process.stdout.write(JSON.stringify(helper(...request.args)))

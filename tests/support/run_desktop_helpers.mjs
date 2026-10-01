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

// A JSON request cannot carry a live JS function, so a callback-shaped argument
// is described declaratively (`{"$resolve": value}` / `{"$reject": message}`)
// and revived here into an actual function that returns a settled promise.
function reviveArgs(args) {
  return args.map(arg => {
    if (arg && typeof arg === 'object' && '$resolve' in arg) {
      return () => Promise.resolve(arg.$resolve)
    }
    if (arg && typeof arg === 'object' && '$reject' in arg) {
      return () => Promise.reject(new Error(arg.$reject))
    }
    return arg
  })
}

let serialized = ''
for await (const chunk of process.stdin) serialized += chunk
const request = JSON.parse(serialized)
const helper = plugin.namespace[request.function]
if (typeof helper !== 'function') throw new Error(`Unknown helper: ${request.function}`)
process.stdout.write(JSON.stringify(await helper(...reviveArgs(request.args))))

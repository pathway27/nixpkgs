import collections
import json
import sys
from typing import Any, Dict, List

JSON = Dict[str, Any]

class Key:
    def __init__(self, path: List[str]):
        self.path = path
    def __hash__(self):
        result = 0
        for id in self.path:
            result ^= hash(id)
        return result
    def __eq__(self, other):
        return type(self) is type(other) and self.path == other.path

Option = collections.namedtuple('Option', ['name', 'value'])

# pivot a dict of options keyed by their display name to a dict keyed by their path
def pivot(options: Dict[str, JSON]) -> Dict[Key, Option]:
    result: Dict[Key, Option] = dict()
    for (name, opt) in options.items():
        result[Key(opt['loc'])] = Option(name, opt)
    return result

# pivot back to indexed-by-full-name
# like the docbook build we'll just fail if multiple options with differing locs
# render to the same option name.
def unpivot(options: Dict[Key, Option]) -> Dict[str, JSON]:
    result: Dict[str, Dict] = dict()
    for (key, opt) in options.items():
        if opt.name in result:
            raise RuntimeError(
                'multiple options with colliding ids found',
                opt.name,
                result[opt.name]['loc'],
                opt.value['loc'],
            )
        result[opt.name] = opt.value
    return result

# converts in-place!
def convertMD(options: Dict[str, Any]) -> str:
    import mistune
    import re
    from xml.sax.saxutils import escape, quoteattr

    admonitions = {
        '.warning': 'warning',
        '.important': 'important',
        '.note': 'note'
    }
    class Renderer(mistune.renderers.BaseRenderer):
        def _get_method(self, name):
            try:
                return super(Renderer, self)._get_method(name)
            except AttributeError:
                def not_supported(children, **kwargs):
                    raise NotImplementedError("md node not supported yet", name, children, **kwargs)
                return not_supported

        def text(self, text):
            return escape(text)
        def paragraph(self, text):
            return text + "\n\n"
        def codespan(self, text):
            return f"<literal>{text}</literal>"
        def block_code(self, text, info=None):
            info = f" language={quoteattr(info)}" if info is not None else ""
            return f"<programlisting{info}>\n{text}</programlisting>"
        def link(self, link, text=None, title=None):
            if link[0:1] == '#':
                attr = "linkend"
                link = quoteattr(link[1:])
            else:
                # try to faithfully reproduce links that were of the form <link href="..."/>
                # in docbook format
                if text == link:
                    text = ""
                attr = "xlink:href"
                link = quoteattr(link)
            return f"<link {attr}={link}>{text}</link>"
        def list(self, text, ordered, level, start=None):
            if ordered:
                raise NotImplementedError("ordered lists not supported yet")
            return f"<itemizedlist>\n{text}\n</itemizedlist>"
        def list_item(self, text, level):
            return f"<listitem><para>{text}</para></listitem>\n"
        def block_text(self, text):
            return text
        def emphasis(self, text):
            return f"<emphasis>{text}</emphasis>"
        def strong(self, text):
            return f"<emphasis role=\"strong\">{text}</emphasis>"
        def admonition(self, text, kind):
            if kind not in admonitions:
                raise NotImplementedError(f"admonition {kind} not supported yet")
            tag = admonitions[kind]
            # we don't keep whitespace here because usually we'll contain only
            # a single paragraph and the original docbook string is no longer
            # available to restore the trailer.
            return f"<{tag}><para>{text.rstrip()}</para></{tag}>"
        def command(self, text):
            return f"<command>{escape(text)}</command>"
        def option(self, text):
            return f"<option>{escape(text)}</option>"
        def file(self, text):
            return f"<filename>{escape(text)}</filename>"
        def manpage(self, page, section):
            title = f"<refentrytitle>{escape(page)}</refentrytitle>"
            vol = f"<manvolnum>{escape(section)}</manvolnum>"
            return f"<citerefentry>{title}{vol}</citerefentry>"

        def finalize(self, data):
            return "".join(data)

    plugins = []

    COMMAND_PATTERN = r'\{command\}`(.*?)`'
    def command(md):
        def parse(self, m, state):
            return ('command', m.group(1))
        md.inline.register_rule('command', COMMAND_PATTERN, parse)
        md.inline.rules.append('command')
    plugins.append(command)

    FILE_PATTERN = r'\{file\}`(.*?)`'
    def file(md):
        def parse(self, m, state):
            return ('file', m.group(1))
        md.inline.register_rule('file', FILE_PATTERN, parse)
        md.inline.rules.append('file')
    plugins.append(file)

    OPTION_PATTERN = r'\{option\}`(.*?)`'
    def option(md):
        def parse(self, m, state):
            return ('option', m.group(1))
        md.inline.register_rule('option', OPTION_PATTERN, parse)
        md.inline.rules.append('option')
    plugins.append(option)

    MANPAGE_PATTERN = r'\{manpage\}`(.*?)\((.+?)\)`'
    def manpage(md):
        def parse(self, m, state):
            return ('manpage', m.group(1), m.group(2))
        md.inline.register_rule('manpage', MANPAGE_PATTERN, parse)
        md.inline.rules.append('manpage')
    plugins.append(manpage)

    ADMONITION_PATTERN = re.compile(r'^::: \{([^\n]*?)\}\n(.*?)^:::\n', flags=re.MULTILINE|re.DOTALL)
    def admonition(md):
        def parse(self, m, state):
            return {
                'type': 'admonition',
                'children': self.parse(m.group(2), state),
                'params': [ m.group(1) ],
            }
        md.block.register_rule('admonition', ADMONITION_PATTERN, parse)
        md.block.rules.append('admonition')
    plugins.append(admonition)

    def convertString(text: str) -> str:
        rendered = mistune.markdown(text, renderer=Renderer(), plugins=plugins)
        # keep trailing spaces so we can diff the generated XML to check for conversion bugs.
        return rendered.rstrip() + text[len(text.rstrip()):]

    def optionIs(option: Dict[str, Any], key: str, typ: str) -> bool:
        if key not in option: return False
        if type(option[key]) != dict: return False
        if '_type' not in option[key]: return False
        return option[key]['_type'] == typ

    for (name, option) in options.items():
        if optionIs(option, 'description', 'mdDoc'):
            option['description'] = convertString(option['description']['text'])
        if optionIs(option, 'example', 'literalMD'):
            docbook = convertString(option['example']['text'])
            option['example'] = { '_type': 'literalDocBook', 'text': docbook }
        if optionIs(option, 'default', 'literalMD'):
            docbook = convertString(option['default']['text'])
            option['default'] = { '_type': 'literalDocBook', 'text': docbook }

    return options

warningsAreErrors = sys.argv[1] == "--warnings-are-errors"
optOffset = 1 if warningsAreErrors else 0
options = pivot(json.load(open(sys.argv[1 + optOffset], 'r')))
overrides = pivot(json.load(open(sys.argv[2 + optOffset], 'r')))

# fix up declaration paths in lazy options, since we don't eval them from a full nixpkgs dir
for (k, v) in options.items():
    # The _module options are not declared in nixos/modules
    if v.value['loc'][0] != "_module":
        v.value['declarations'] = list(map(lambda s: f'nixos/modules/{s}', v.value['declarations']))

# merge both descriptions
for (k, v) in overrides.items():
    cur = options.setdefault(k, v).value
    for (ok, ov) in v.value.items():
        if ok == 'declarations':
            decls = cur[ok]
            for d in ov:
                if d not in decls:
                    decls += [d]
        elif ok == "type":
            # ignore types of placeholder options
            if ov != "_unspecified" or cur[ok] == "_unspecified":
                cur[ok] = ov
        elif ov is not None or cur.get(ok, None) is None:
            cur[ok] = ov

severity = "error" if warningsAreErrors else "warning"

# check that every option has a description
hasWarnings = False
for (k, v) in options.items():
    if v.value.get('description', None) is None:
        hasWarnings = True
        print(f"\x1b[1;31m{severity}: option {v.name} has no description\x1b[0m", file=sys.stderr)
        v.value['description'] = "This option has no description."
    if v.value.get('type', "unspecified") == "unspecified":
        hasWarnings = True
        print(
            f"\x1b[1;31m{severity}: option {v.name} has no type. Please specify a valid type, see " +
            "https://nixos.org/manual/nixos/stable/index.html#sec-option-types\x1b[0m", file=sys.stderr)

if hasWarnings and warningsAreErrors:
    print(
        "\x1b[1;31m" +
        "Treating warnings as errors. Set documentation.nixos.options.warningsAreErrors " +
        "to false to ignore these warnings." +
        "\x1b[0m",
        file=sys.stderr)
    sys.exit(1)

json.dump(convertMD(unpivot(options)), fp=sys.stdout)

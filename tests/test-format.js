// Runnable check for format.js against the real `ai-usage --json` output, using the shell's own JS engine.
//   gjs -m /home/rubens/.hermes/cache/scratch/test-format.js
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import System from 'system';

const EXT = '/home/rubens/Development/agents-usage/gnome-extension';
const { renderLines, bar, esc } = await import(`file://${EXT}/format.js`);

let fails = 0;
const ok = (cond, msg) => {
    if (!cond) {
        fails++;
        printerr(`FAIL ${msg}`);
    }
};

// pure functions
ok(bar(0) === '\u2591'.repeat(10), 'bar(0) all empty');
ok(bar(100) === '\u2593'.repeat(10), 'bar(100) all filled');
ok(bar(55).length === 10 && bar(-3) === bar(0), 'bar width clamped');
ok(esc('a<b>&"c"') === 'a&lt;b&gt;&amp;"c"', `esc: ${esc('a<b>&"c"')}`);

// real subprocess, same call shape the extension uses
const proc = Gio.Subprocess.new(['/home/rubens/.local/bin/ai-usage', '--json'],
    Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
const res = proc.communicate_utf8(null, null);
print(`communicate_utf8() -> ${Array.isArray(res) ? 'array of ' + res.length : typeof res}`);
const stdout = Array.isArray(res) ? res[1] : res;
ok(proc.get_successful(), 'ai-usage exited 0');
const data = JSON.parse(stdout);
ok(Array.isArray(data) && data.length === 4, `4 providers, got ${data.length}`);
ok(data.some(d => d.key === 'deepseek'), 'deepseek present');

// the extension uses the async form: confirm the tuple shape so the destructuring is right
Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async', 'communicate_utf8_finish');
const proc2 = Gio.Subprocess.new(['/home/rubens/.local/bin/ai-usage', '--json'],
    Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
const ares = await proc2.communicate_utf8_async(null, null);
print(`promisified async -> array of ${ares.length}: [${ares.map(x => typeof x).join(', ')}]`);
print(`  first element: ${JSON.stringify(ares[0]).slice(0, 40)}`);

const { blocks, worst, alert } = renderLines(data);
ok(blocks.length === data.length, 'one block per provider');
ok(blocks.every(b => b.markup.length > 0), 'no empty markup');
ok(worst >= 0 && worst <= 100, `worst in range (${worst})`);

// markup must be well-formed XML: dump for the python validator
let dump = '';
for (const b of blocks) {
    dump += `<block>${b.markup}</block>\n`;
    print('----- rendered block -----');
    print(b.markup.replace(/<[^>]+>/g, ''));
}
GLib.file_set_contents('/home/rubens/.hermes/cache/scratch/markup.xml', `<root>\n${dump}</root>\n`);

print(`\nworst=${worst}% alert=${alert}`);
print(fails === 0 ? 'gjs selftest ok' : `${fails} FAILURES`);
System.exit(fails === 0 ? 0 : 1);

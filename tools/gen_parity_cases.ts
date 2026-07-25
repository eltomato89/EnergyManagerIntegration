/**
 * Erzeugt Referenzwerte aus der Kartenimplementierung.
 *
 * Kein echter Test — nutzt nur die vitest-Umgebung, um TypeScript auszuführen
 * und die Ergebnisse als JSON abzulegen. Die Integration prüft sich dagegen.
 */
import { writeFileSync } from 'node:fs';
import { it } from 'vitest';
import { computeSurplus } from '../../src/lib/surplus';
import { TimeWeightedWindow } from '../../src/lib/smoothing';
import type { SurplusInput } from '../../src/types/runtime';

const OUT = process.env.CASES_OUT!;

interface Case {
  name: string;
  input: Record<string, unknown>;
}

// Systematisch über die Achsen: Modus, Batterievorzeichen, Batteriemodus,
// Reserve, Ladestandsgrenze, Fehlerfälle.
const CASES: Case[] = [];

for (const grid of [-3200, -2000, -7, 0, 7, 800, 2500]) {
  for (const battery of [null, -1500, -386, 0, 600, 1500]) {
    for (const mode of ['charge_only', 'full'] as const) {
      for (const reserve of [0, 300]) {
        CASES.push({
          name: `grid=${grid} bat=${battery} mode=${mode} res=${reserve}`,
          input: {
            mode: 'grid',
            grid: { w: grid },
            production: { w: null, reason: 'missing' },
            consumption: { w: null, reason: 'missing' },
            battery: battery === null ? { w: null, reason: 'missing' } : { w: battery },
            batteryConfigured: battery !== null,
            batteryMode: mode,
            batterySoc: 62,
            consumptionIncludesBattery: false,
            batteryReserveW: reserve,
          },
        });
      }
    }
  }
}

for (const prod of [0, 463, 5000]) {
  for (const cons of [842, 1800, 3000]) {
    for (const incl of [false, true]) {
      CASES.push({
        name: `split prod=${prod} cons=${cons} incl=${incl}`,
        input: {
          mode: 'split',
          grid: { w: null, reason: 'missing' },
          production: { w: prod },
          consumption: { w: cons },
          battery: { w: 800 },
          batteryConfigured: true,
          batteryMode: 'charge_only',
          batterySoc: 50,
          consumptionIncludesBattery: incl,
          batteryReserveW: 0,
        },
      });
    }
  }
}

// Ladestandsgrenze
for (const soc of [5, 19, 20, 21, 84]) {
  CASES.push({
    name: `soc=${soc} minSoc=20`,
    input: {
      mode: 'grid',
      grid: { w: -2000 },
      production: { w: null, reason: 'missing' },
      consumption: { w: null, reason: 'missing' },
      battery: { w: 0 },
      batteryConfigured: true,
      batteryMode: 'charge_only',
      batterySoc: soc,
      consumptionIncludesBattery: false,
      batteryMinSoc: 20,
      batteryReserveW: 0,
    },
  });
}

it('erzeugt Referenzwerte', () => {
  const surplus = CASES.map((c) => {
    const r = computeSurplus(c.input as unknown as SurplusInput);
    return {
      name: c.name,
      input: c.input,
      expected: {
        raw: r.raw,
        available: r.available,
        batteryCorrection: r.batteryCorrection,
        gridW: r.gridW,
        batteryW: r.batteryW,
        degraded: r.degraded,
        errors: r.errors,
      },
    };
  });

  // Glättung: Folgen von (Zeitversatz, Wert) und der erwartete Mittelwert.
  const smoothingCases = [
    { name: '55s@3000 + 5s@0', window: 60, samples: [[0, 3000], [55, 0]], at: 60 },
    { name: 'konstant, carry-in', window: 60, samples: [[0, 2200]], at: 300 },
    { name: 'halb/halb', window: 60, samples: [[0, 0], [100, 1000]], at: 130 },
    { name: 'nach dem Wechsel', window: 60, samples: [[0, 0], [100, 1000]], at: 160 },
    { name: 'Luecke', window: 60, samples: [[0, 2000], [30, null]], at: 60 },
    { name: 'Fenster 0', window: 0, samples: [[0, 1000], [1, 250]], at: 2 },
  ];

  const smoothing = smoothingCases.map((c) => {
    const w = new TimeWeightedWindow(c.window * 1000);
    for (const [dt, v] of c.samples) {
      w.push(v as number | null, (dt as number) * 1000);
    }
    return {
      name: c.name,
      window: c.window,
      samples: c.samples,
      at: c.at,
      expected: w.value(c.at * 1000),
      coverage: w.coverage(c.at * 1000),
    };
  });

  writeFileSync(OUT, JSON.stringify({ surplus, smoothing }, null, 2));
});

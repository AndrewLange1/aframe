import logging
from pathlib import Path
from typing import Optional

import numpy as np
from astropy.cosmology import Planck15 as cosmology
from bokeh.io import save
from bokeh.layouts import gridplot
from plots import compute, utils
from plots.gwtc3 import catalog_results
from typeo import scriptify

from aframe.analysis.ledger.events import (
    RecoveredInjectionSet,
    TimeSlideEventSet,
)
from aframe.analysis.ledger.injections import InjectionParameterSet
from aframe.logging import configure_logging
from aframe.priors.priors import end_o3_ratesandpops, log_normal_masses


def get_prob(prior, ledger):
    sample = dict(mass_1=ledger.mass_1, mass_2=ledger.mass_2)
    return prior.prob(sample, axis=0)


@scriptify
def main(
    background_file: Path,
    foreground_file: Path,
    rejected_params: Path,
    output_fname: Path,
    log_file: Optional[Path] = None,
    max_far: float = 1000,
    sigma: float = 0.1,
    verbose: bool = False,
):
    configure_logging(log_file, verbose)

    logging.info("Reading in inference outputs")
    background = TimeSlideEventSet.read(background_file)
    foreground = RecoveredInjectionSet.read(foreground_file)
    rejected_params = InjectionParameterSet.read(rejected_params)

    for i in range(2):
        mass = f"mass_{i + 1}"
        for ledger in [foreground, rejected_params]:
            val = getattr(ledger, mass)
            setattr(ledger, mass, val / (1 + ledger.redshift))
    logging.info("Read in:")
    logging.info(f"\t{len(background)} background events")
    logging.info(f"\t{len(foreground)} foreground events")
    logging.info(f"\t{len(rejected_params)} rejected events")

    logging.info("Computing data likelihood under source prior")
    source, _ = end_o3_ratesandpops(cosmology)
    source_probs = get_prob(source, foreground)
    source_rejected_probs = get_prob(source, rejected_params)

    logging.info("Computing maximum astrophysical volume")
    zprior = source["redshift"]
    zmin, zmax = zprior.minimum, zprior.maximum

    try:
        decprior = source["dec"]
    except KeyError:
        decrange = None
    else:
        decrange = (decprior.minimum, decprior.maximum)
    v0 = utils.get_astrophysical_volume(zmin, zmax, cosmology, decrange)
    v0 /= 10**9

    Tb = background.Tb / utils.SECONDS_PER_MONTH
    max_events = int(max_far * Tb)
    x = np.arange(1, max_events + 1) / Tb
    thresholds = np.sort(background.detection_statistic)[-max_events:][::-1]

    mass_combos = [(35, 35), (35, 20), (20, 20), (20, 10)]

    weights = np.zeros((4, len(source_probs)))
    for i, combo in enumerate(mass_combos):
        logging.info(f"Computing likelihoods under {combo} log normal")
        prior, _ = log_normal_masses(*combo, sigma=sigma, cosmology=cosmology)

        prob = get_prob(prior, foreground)
        rejected_prob = get_prob(prior, rejected_params)

        weight = prob / source_probs
        rejected_weights = rejected_prob / source_rejected_probs
        norm = weight.sum() + rejected_weights.sum()
        weight /= norm
        weights[i] = weight

    logging.info("Computing sensitive volume at thresholds")
    y, err = compute.sensitive_volume(
        foreground.detection_statistic, weights, thresholds
    )
    y *= v0
    err *= v0

    plots = utils.make_grid(mass_combos)
    for i, (p, color) in enumerate(zip(plots, utils.palette)):
        p.line(x, y[i], line_width=1.5, line_color=color)
        utils.plot_err_bands(
            p,
            x,
            y[i],
            err[i],
            line_color=color,
            line_width=0.8,
            fill_color=color,
            fill_alpha=0.4,
        )

        for pipeline, data in catalog_results.items():
            # convert VT to volume by dividing out years
            vt = data["vt"][mass_combos[i]]
            v = vt * 365 / data["Tb"]

            # only include a legend on the top left
            kwargs = {}
            if i == 0:
                kwargs["legend_label"] = pipeline
            p.line(
                [x[0], x[-1]],
                [v, v],
                line_color="#333333",
                line_dash=data["dash"],
                line_alpha=0.7,
                line_width=2,
                **kwargs,
            )

            # style the legend on the top left plot
            if i == 0:
                # style legend position
                p.legend.location = "top_left"
                p.legend.margin = 4
                p.legend.padding = 2

                # style individual glyphs
                p.legend.glyph_height = 6
                p.legend.label_text_font_size = "8pt"
                p.legend.label_height = 8

                # style title
                p.legend.title = "GWTC-3 comparisons"
                p.legend.title_text_font_size = "9pt"
                p.legend.title_text_font_style = "bold"

    grid = gridplot(plots, toolbar_location="right", ncols=2)
    save(grid, filename=output_fname)


if __name__ == "__main__":
    main()

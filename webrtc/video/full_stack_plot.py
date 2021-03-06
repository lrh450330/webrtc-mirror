#!/usr/bin/env python
# Copyright (c) 2015 The WebRTC project authors. All Rights Reserved.
#
# Use of this source code is governed by a BSD-style license
# that can be found in the LICENSE file in the root of the source
# tree. An additional intellectual property rights grant can be found
# in the file PATENTS.  All contributing project authors may
# be found in the AUTHORS file in the root of the source tree.

"""Generate graphs for data generated by loopback tests.

Usage examples:
  Show end to end time for a single full stack test.
  ./full_stack_plot.py -df end_to_end -o 600 --frames 1000 vp9_data.txt

  Show simultaneously PSNR and encoded frame size for two different runs of
  full stack test. Averaged over a cycle of 200 frames. Used e.g. for
  screenshare slide test.
  ./full_stack_plot.py -c 200 -df psnr -drf encoded_frame_size \\
                       before.txt after.txt

  Similar to the previous test, but multiple graphs.
  ./full_stack_plot.py -c 200 -df psnr vp8.txt vp9.txt --next \\
                       -c 200 -df sender_time vp8.txt vp9.txt --next \\
                       -c 200 -df end_to_end vp8.txt vp9.txt
"""

import argparse
from collections import defaultdict
import itertools
import sys
import matplotlib.pyplot as plt
import numpy

# Fields
DROPPED = 0
INPUT_TIME = 1              # ms
SEND_TIME = 2               # ms
RECV_TIME = 3               # ms
ENCODED_FRAME_SIZE = 4      # bytes
PSNR = 5
SSIM = 6
RENDER_TIME = 7             # ms

TOTAL_RAW_FIELDS = 8

SENDER_TIME = TOTAL_RAW_FIELDS + 0
RECEIVER_TIME = TOTAL_RAW_FIELDS + 1
END_TO_END = TOTAL_RAW_FIELDS + 2
RENDERED_DELTA = TOTAL_RAW_FIELDS + 3

FIELD_MASK = 255

# Options
HIDE_DROPPED = 256
RIGHT_Y_AXIS = 512

# internal field id, field name, title
_fields = [
    # Raw
    (DROPPED, "dropped", "dropped"),
    (INPUT_TIME, "input_time_ms", "input time"),
    (SEND_TIME, "send_time_ms", "send time"),
    (RECV_TIME, "recv_time_ms", "recv time"),
    (ENCODED_FRAME_SIZE, "encoded_frame_size", "encoded frame size"),
    (PSNR, "psnr", "PSNR"),
    (SSIM, "ssim", "SSIM"),
    (RENDER_TIME, "render_time_ms", "render time"),
    # Auto-generated
    (SENDER_TIME, "sender_time", "sender time"),
    (RECEIVER_TIME, "receiver_time", "receiver time"),
    (END_TO_END, "end_to_end", "end to end"),
    (RENDERED_DELTA, "rendered_delta", "rendered delta"),
]

name_to_id = {field[1]: field[0] for field in _fields}
id_to_title = {field[0]: field[2] for field in _fields}

def field_arg_to_id(arg):
  if arg == "none":
    return None
  if arg in name_to_id:
    return name_to_id[arg]
  if arg + "_ms" in name_to_id:
    return name_to_id[arg + "_ms"]
  raise Exception("Unrecognized field name \"{}\"".format(arg))


class PlotLine(object):
  """Data for a single graph line."""

  def __init__(self, label, values, flags):
    self.label = label
    self.values = values
    self.flags = flags


class Data(object):
  """Object representing one full stack test."""

  def __init__(self, filename):
    self.title = ""
    self.length = 0
    self.samples = defaultdict(list)

    self._read_samples(filename)

  def _read_samples(self, filename):
    """Reads graph data from the given file."""
    f = open(filename)
    it = iter(f)

    self.title = it.next().strip()
    self.length = int(it.next())
    field_names = [name.strip() for name in it.next().split()]
    field_ids = [name_to_id[name] for name in field_names]

    for field_id in field_ids:
      self.samples[field_id] = [0.0] * self.length

    for sample_id in xrange(self.length):
      for col, value in enumerate(it.next().split()):
        self.samples[field_ids[col]][sample_id] = float(value)

    self._subtract_first_input_time()
    self._generate_additional_data()

    f.close()

  def _subtract_first_input_time(self):
    offset = self.samples[INPUT_TIME][0]
    for field in [INPUT_TIME, SEND_TIME, RECV_TIME, RENDER_TIME]:
      if field in self.samples:
        self.samples[field] = [x - offset for x in self.samples[field]]

  def _generate_additional_data(self):
    """Calculates sender time, receiver time etc. from the raw data."""
    s = self.samples
    last_render_time = 0
    for field_id in [SENDER_TIME, RECEIVER_TIME, END_TO_END, RENDERED_DELTA]:
      s[field_id] = [0] * self.length

    for k in range(self.length):
      s[SENDER_TIME][k] = s[SEND_TIME][k] - s[INPUT_TIME][k]

      decoded_time = s[RENDER_TIME][k]
      s[RECEIVER_TIME][k] = decoded_time - s[RECV_TIME][k]
      s[END_TO_END][k] = decoded_time - s[INPUT_TIME][k]
      if not s[DROPPED][k]:
        if k > 0:
          s[RENDERED_DELTA][k] = decoded_time - last_render_time
        last_render_time = decoded_time

  def _hide(self, values):
    """
    Replaces values for dropped frames with None.
    These values are then skipped by the plot() method.
    """

    return [None if self.samples[DROPPED][k] else values[k]
            for k in range(len(values))]

  def add_samples(self, config, target_lines_list):
    """Creates graph lines from the current data set with given config."""
    for field in config.fields:
      # field is None means the user wants just to skip the color.
      if field is None:
        target_lines_list.append(None)
        continue

      field_id = field & FIELD_MASK
      values = self.samples[field_id]

      if field & HIDE_DROPPED:
        values = self._hide(values)

      target_lines_list.append(PlotLine(
          self.title + " " + id_to_title[field_id],
          values, field & ~FIELD_MASK))


def average_over_cycle(values, length):
  """
  Returns the list:
    [
        avg(values[0], values[length], ...),
        avg(values[1], values[length + 1], ...),
        ...
        avg(values[length - 1], values[2 * length - 1], ...),
    ]

  Skips None values when calculating the average value.
  """

  total = [0.0] * length
  count = [0] * length
  for k in range(len(values)):
    if values[k] is not None:
      total[k % length] += values[k]
      count[k % length] += 1

  result = [0.0] * length
  for k in range(length):
    result[k] = total[k] / count[k] if count[k] else None
  return result


class PlotConfig(object):
  """Object representing a single graph."""

  def __init__(self, fields, data_list, cycle_length=None, frames=None,
               offset=0, output_filename=None, title="Graph"):
    self.fields = fields
    self.data_list = data_list
    self.cycle_length = cycle_length
    self.frames = frames
    self.offset = offset
    self.output_filename = output_filename
    self.title = title

  def plot(self, ax1):
    lines = []
    for data in self.data_list:
      if not data:
        # Add None lines to skip the colors.
        lines.extend([None] * len(self.fields))
      else:
        data.add_samples(self, lines)

    def _slice_values(values):
      if self.offset:
        values = values[self.offset:]
      if self.frames:
        values = values[:self.frames]
      return values

    length = None
    for line in lines:
      if line is None:
        continue

      line.values = _slice_values(line.values)
      if self.cycle_length:
        line.values = average_over_cycle(line.values, self.cycle_length)

      if length is None:
        length = len(line.values)
      elif length != len(line.values):
        raise Exception("All arrays should have the same length!")

    ax1.set_xlabel("Frame", fontsize="large")
    if any(line.flags & RIGHT_Y_AXIS for line in lines if line):
      ax2 = ax1.twinx()
      ax2.set_xlabel("Frame", fontsize="large")
    else:
      ax2 = None

    # Have to implement color_cycle manually, due to two scales in a graph.
    color_cycle = ["b", "r", "g", "c", "m", "y", "k"]
    color_iter = itertools.cycle(color_cycle)

    for line in lines:
      if not line:
        color_iter.next()
        continue

      if self.cycle_length:
        x = numpy.array(range(self.cycle_length))
      else:
        x = numpy.array(range(self.offset, self.offset + len(line.values)))
      y = numpy.array(line.values)
      ax = ax2 if line.flags & RIGHT_Y_AXIS else ax1
      ax.plot(x, y, "o-", label=line.label, markersize=3.0, linewidth=1.0,
              color=color_iter.next())

    ax1.grid(True)
    if ax2:
      ax1.legend(loc="upper left", shadow=True, fontsize="large")
      ax2.legend(loc="upper right", shadow=True, fontsize="large")
    else:
      ax1.legend(loc="best", shadow=True, fontsize="large")


def load_files(filenames):
  result = []
  for filename in filenames:
    if filename in load_files.cache:
      result.append(load_files.cache[filename])
    else:
      data = Data(filename)
      load_files.cache[filename] = data
      result.append(data)
  return result
load_files.cache = {}


def get_parser():
  class CustomAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
      if "ordered_args" not in namespace:
        namespace.ordered_args = []
      namespace.ordered_args.append((self.dest, values))

  parser = argparse.ArgumentParser(
      description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

  parser.add_argument(
      "-c", "--cycle_length", nargs=1, action=CustomAction,
      type=int, help="Cycle length over which to average the values.")
  parser.add_argument(
      "-f", "--field", nargs=1, action=CustomAction,
      help="Name of the field to show. Use 'none' to skip a color.")
  parser.add_argument("-r", "--right", nargs=0, action=CustomAction,
                      help="Use right Y axis for given field.")
  parser.add_argument("-d", "--drop", nargs=0, action=CustomAction,
                      help="Hide values for dropped frames.")
  parser.add_argument("-o", "--offset", nargs=1, action=CustomAction, type=int,
                      help="Frame offset.")
  parser.add_argument("-n", "--next", nargs=0, action=CustomAction,
                      help="Separator for multiple graphs.")
  parser.add_argument(
      "--frames", nargs=1, action=CustomAction, type=int,
      help="Frame count to show or take into account while averaging.")
  parser.add_argument("-t", "--title", nargs=1, action=CustomAction,
                      help="Title of the graph.")
  parser.add_argument(
      "-O", "--output_filename", nargs=1, action=CustomAction,
      help="Use to save the graph into a file. "
           "Otherwise, a window will be shown.")
  parser.add_argument(
      "files", nargs="+", action=CustomAction,
      help="List of text-based files generated by loopback tests.")
  return parser


def _plot_config_from_args(args, graph_num):
  # Pylint complains about using kwargs, so have to do it this way.
  cycle_length = None
  frames = None
  offset = 0
  output_filename = None
  title = "Graph"

  fields = []
  files = []
  mask = 0
  for key, values in args:
    if key == "cycle_length":
      cycle_length = values[0]
    elif key == "frames":
      frames = values[0]
    elif key == "offset":
      offset = values[0]
    elif key == "output_filename":
      output_filename = values[0]
    elif key == "title":
      title = values[0]
    elif key == "drop":
      mask |= HIDE_DROPPED
    elif key == "right":
      mask |= RIGHT_Y_AXIS
    elif key == "field":
      field_id = field_arg_to_id(values[0])
      fields.append(field_id | mask if field_id is not None else None)
      mask = 0  # Reset mask after the field argument.
    elif key == "files":
      files.extend(values)

  if not files:
    raise Exception("Missing file argument(s) for graph #{}".format(graph_num))
  if not fields:
    raise Exception("Missing field argument(s) for graph #{}".format(graph_num))

  return PlotConfig(fields, load_files(files), cycle_length=cycle_length,
      frames=frames, offset=offset, output_filename=output_filename,
      title=title)


def plot_configs_from_args(args):
  """Generates plot configs for given command line arguments."""
  # The way it works:
  #   First we detect separators -n/--next and split arguments into groups, one
  #   for each plot. For each group, we partially parse it with
  #   argparse.ArgumentParser, modified to remember the order of arguments.
  #   Then we traverse the argument list and fill the PlotConfig.
  args = itertools.groupby(args, lambda x: x in ["-n", "--next"])
  args = list(list(group) for match, group in args if not match)

  parser = get_parser()
  plot_configs = []
  for index, raw_args in enumerate(args):
    graph_args = parser.parse_args(raw_args).ordered_args
    plot_configs.append(_plot_config_from_args(graph_args, index))
  return plot_configs


def show_or_save_plots(plot_configs):
  for config in plot_configs:
    fig = plt.figure(figsize=(14.0, 10.0))
    ax = fig.add_subplot(1, 1, 1)

    plt.title(config.title)
    config.plot(ax)
    if config.output_filename:
      print "Saving to", config.output_filename
      fig.savefig(config.output_filename)
      plt.close(fig)

  plt.show()

if __name__ == "__main__":
  show_or_save_plots(plot_configs_from_args(sys.argv[1:]))

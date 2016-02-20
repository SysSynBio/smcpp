from gooey import Gooey, GooeyParser

from .common import init_subparsers

@Gooey(progress_regex=r'.*EM iteration (\d+)/(\d+)',
        progress_expr='x[0] / x[1] * 100',
        monospace_display=True,
        disable_progress_bar_animation=True)
def main():
    parser = GooeyParser()
    subparsers = parser.add_subparsers()
    init_subparsers(subparsers)
    args = parser.parse_args()
    args.func(args)
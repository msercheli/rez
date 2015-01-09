from rez.packages_ import get_developer_package
from rez.exceptions import BuildProcessError, BuildContextResolveError
from rez.resolved_context import ResolvedContext
from rez.release_hook import create_release_hooks
from rez.resolver import ResolverStatus
from rez.config import config
from rez.vendor.enum import Enum
import os.path


def get_build_process_types():
    """Returns the available build process implementations."""
    from rez.plugin_managers import plugin_manager
    return plugin_manager.get_plugins('build_process')


def create_build_process(process_type, working_dir, build_system, vcs=None,
                         ensure_latest=True, verbose=False):
    """Create a `BuildProcess` instance."""
    from rez.plugin_managers import plugin_manager
    process_types = get_build_process_types()
    if process_type not in process_type:
        raise BuildProcessError("Unknown build process: %r" % process_type)
    cls = plugin_manager.get_plugin_class('build_process', process_type)

    return cls(working_dir,
               build_system=build_system,
               vcs=vcs,
               ensure_latest=ensure_latest,
               verbose=verbose)


class BuildType(Enum):
    """ Enum to represent the type of build."""
    local = 0
    central = 1


class BuildProcess(object):
    """A BuildProcess builds and possibly releases a package.

    A build process iterates over the variants of a package, creates the
    correct build environment for each variant, builds that variant using a
    build system (or possibly creates a script so the user can do that
    independently), and then possibly releases the package with the nominated
    VCS. This is an abstract base class, you should use a BuildProcess
    subclass.
    """
    @classmethod
    def name(cls):
        raise NotImplementedError

    def __init__(self, working_dir, build_system, vcs=None, ensure_latest=True,
                 verbose=False):
        """Create a BuildProcess.

        Args:
            working_dir (str): Directory containing the package to build.
            build_system (`BuildSystem`): Build system used to build the package.
            vcs (`ReleaseVCS`): Version control system to use for the release
                process. If None, the package will only be built, not released.
            ensure_latest: If True, do not allow the release process to occur
                if an newer versioned package is already released.
        """
        self.verbose = verbose
        self.working_dir = working_dir
        self.build_system = build_system
        self.vcs = vcs
        self.ensure_latest = ensure_latest

        if vcs and vcs.path != working_dir:
            raise BuildProcessError(
                "Build process was instantiated with a mismatched VCS instance")

        self.debug_print = config.debug_printer("package_release")
        self.package = get_developer_package(working_dir)
        hook_names = self.package.config.release_hooks or []
        self.hooks = create_release_hooks(hook_names, working_dir)
        self.build_path = os.path.join(self.working_dir,
                                       self.package.config.build_directory)

    def build(self, install_path=None, clean=False, install=False, variants=None):
        """Perform the build process.

        Iterates over the package's variants, resolves the environment for
        each, and runs the build system within each resolved environment.

        Args:
            install_path (str): The package repository path to install the
                package to, if installing. If None, defaults to
                `config.local_packages_path`.
            clean (bool): If True, clear any previous build first. Otherwise,
                rebuild over the top of a previous build.
            install (bool): If True, install the build.
            variants (list of int): Indexes of variants to build, all if None.

        Raises:
            `BuildError`: If the build failed.
        """
        raise NotImplementedError

    def release(self, release_message=None, variants=None):
        """Perform the release process.

        Iterates over the package's variants, building and installing each into
        the release path determined by `config.release_packages_path`.

        Args:
            release_message (str): Message to associate with the release.
            variants (list of int): Indexes of variants to release, all if None.

        Raises:
            `ReleaseError`: If the release failed.
        """
        raise NotImplementedError


class BuildProcessHelper(BuildProcess):
    """A BuildProcess base class with some useful functionality.
    """
    def visit_variants(self, func, variants=None, **kwargs):
        """Iterate over variants and call a function on each."""
        if variants:
            present_variants = range(self.package.num_variants)
            invalid_variants = set(variants) - set(present_variants)
            if invalid_variants:
                raise BuildError(
                    "The package does not contain the variants: %s"
                    % ", ".join(str(x) for x in sorted(invalid_variants)))

        # iterate over variants
        results = []
        num_visited = 0

        for variant in self.package.iter_variants():
            if variants and variant.index not in variants:
                self._print_header("Skipping %s..." % self._n_of_m(variant))
                continue

            result = func(variant, **kwargs)
            results.append(result)
            num_visited += 1

        return num_visited, results

    def get_package_install_path(self, path):
        """Return the installation path for a package (where its payload goes).
        """
        path_ = os.path.join(path, self.package.name)
        if self.package.version:
            path_ = os.path.join(path_, str(self.package.version))
        return path_

    def create_build_context(self, variant, build_type, build_path):
        """Create a context to build the variant within."""
        request = variant.get_requires(build_requires=True,
                                       private_build_requires=True)

        requests_str = ' '.join(map(str, request))
        self._print("Resolving build environment: %s", requests_str)
        if build_type == BuildType.local:
            packages_path = self.package.config.packages_path
        else:
            packages_path = self.package.config.nonlocal_packages_path

        context = ResolvedContext(request,
                                  package_paths=packages_path,
                                  building=True)
        if self.verbose:
            context.print_info()

        # save context before possible fail, so user can debug
        rxt_filepath = os.path.join(build_path, "build.rxt")
        context.save(rxt_filepath)

        if context.status != ResolverStatus.solved:
            raise BuildContextResolveError(context)
        return context, rxt_filepath

    def _print(self, txt, *nargs):
        if self.verbose:
            if nargs:
                txt = txt % nargs
            print txt

    def _print_header(self, txt, n=1):
        self._print('')
        if n <= 1:
            self._print('-' * 80)
            self._print(txt)
            self._print('-' * 80)
        else:
            self._print(txt)
            self._print('-' * len(txt))

    def _n_of_m(self, variant):
        num_variants = max(self.package.num_variants, 1)
        index = (variant.index or 0) + 1
        return "%d/%d" % (index, num_variants)
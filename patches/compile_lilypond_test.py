#!/usr/bin/env python
import sys
import shutil
import os
import os.path
import datetime
import subprocess

# TODO: add timing information


# enable a ramdisk
# 1. copy this line into /etc/fstab:
#      tmpfs /tmp/ramdisk tmpfs size=700M,user,exec 0 0
#    (use no # when you put into /etc/fstab)
# 2. type:
#      mount /tmp/ramdisk

# OPTIONAL: increase the size=700M to size=2048M and enable this:
BUILD_ALL_DOCS = False
#EXTRA_MAKE_OPTIONS = ""
EXTRA_MAKE_OPTIONS = " -j3 CPU_COUNT=3 "

# this 
AUTO_COMPILE_RESULTS_DIR = "~/lilypond-auto-compile-results/"

try:
    GIT_REPOSITORY_DIR = os.environ["LILYPOND_GIT"]
except:
    print "You must have an environment variable $LILYPOND_GIT"
    sys.exit(1)
SRC_BUILD_DIR = "/tmp/ramdisk"
PREVIOUS_GOOD_COMMIT_FILENAME = "previous_good_commit.txt"
MAIN_LOG_FILENAME = "log-%s.txt"

class AutoCompile():
    ### setup
    def __init__(self):
        #self.date = datetime.datetime.now().strftime("%Y-%m-%d-%H-%m-%S")
        self.date = datetime.datetime.now().strftime("%Y-%m-%d-%H")
        self.git_repository_dir = os.path.expanduser(GIT_REPOSITORY_DIR)
        self.auto_compile_dir = os.path.expanduser(AUTO_COMPILE_RESULTS_DIR)
        if not os.path.exists(self.auto_compile_dir):
            os.mkdir(self.auto_compile_dir)
        self.src_build_dir = os.path.expanduser(SRC_BUILD_DIR)
        self.src_dir = os.path.join(self.src_build_dir,
                                      'src-' + self.date)
        self.build_dir = os.path.join(self.src_dir, 'build')
        self.commit = self.get_head()
        self.prev_good_commit = self.get_previous_good_commit()
        self.main_logfile = os.path.join(
            self.auto_compile_dir,
                str(MAIN_LOG_FILENAME % self.date))
        logfile = open(self.main_logfile, 'a')
        logfile.write("Begin LilyPond compile, commit:\t%s\n" % self.commit)
        logfile.close()

    def debug(self):
        """ prints member variables """
        for key, value in self.__dict__.iteritems():
            print "%-20s %s" % (key, value)

    def get_head(self):
        os.chdir(self.git_repository_dir)
        cmd = "git rev-parse HEAD"
        p = subprocess.Popen(cmd.split(), stdout=subprocess.PIPE)
        head = p.communicate()[0].strip()
        return head

    def get_previous_good_commit(self):
        try:
            previous_good_commit_file = open(os.path.join(
                self.auto_compile_dir,
                PREVIOUS_GOOD_COMMIT_FILENAME))
            prev_good_commit = previous_good_commit_file.read().split()[0]
        except IOError:
            prev_good_commit = ''
        return prev_good_commit

    def write_good_commit(self):
        outfile = open(os.path.join(os.path.join(
                       self.auto_compile_dir,
                       PREVIOUS_GOOD_COMMIT_FILENAME)), 'w')
        outfile.write(self.commit)

    def add_success(self, name):
        logfile = open(self.main_logfile, 'a')
        logfile.write("\tSuccess:\t\t%s\n" % name)
        logfile.close()

    def failed_build(self, name):
        logfile = open(self.main_logfile, 'a')
        logfile.write("*** FAILED BUILD ***\n")
        logfile.write("\t%s\n" % name)
        logfile.write("\tPrevious good commit:\t%s\n" % self.prev_good_commit)
        logfile.write("\tCurrent broken commit:\t%s\n" % self.commit)
        logfile.close()
        ### TODO: send email to -devel ?
        #cmd = "mail -s \"Failed build with %s\" lilypond-devel@gnu.org < %s" % (self.commit, self.main_logfile)

    def failed_step(self, name, message):
        (self.main_logfile, 'a')
        logfile.write("*** FAILED STEP ***\n")
        logfile.write("\t%s\n" % name)
        logfile.write("\t%s\n" % message)
        logfile.close()

    ### actual building
    def make_directories(self):
        if os.path.exists(self.src_dir):
            shutil.rmtree(self.src_dir)
        os.chdir(self.git_repository_dir)
        cmd = "git checkout-index -a --prefix=%s/ " % (self.src_dir)
        os.system(cmd)
        os.makedirs(self.build_dir)

    def runner(self, dirname, command, name=None):
        if not name:
            name = command.replace(" ", "-")
        logfile = open(os.path.join(self.src_dir, 'log-%s.txt'%name), 'w')
        os.chdir(dirname)
        p = subprocess.Popen(command.split(), stdout=logfile, stderr=logfile)
        p.wait()
        returncode = p.returncode
        logfile.close()
        if returncode != 0:
            self.failed_build(command)
            raise Exception("Failed runner: %s", command)
        else:
            self.add_success(command)

    def prep(self):
        self.make_directories()
        # ick, nice-ify this!
        self.runner(self.src_dir, "./autogen.sh --noconfigure", "autogen.sh")
        self.runner(self.build_dir, "../configure", "configure")

    def patch(self, filename, reverse=False):
        os.chdir(self.src_dir)
        reverse = "--reverse" if reverse else ""
        cmd = "patch -f %s -s -p1 < %s" % (reverse, filename)
        returncode = os.system(cmd)
        if returncode != 0:
            self.failed_step("patch", filename)
            raise Exception("Failed patch: %s" % filename)
        self.add_success("applied patch %s" % filename)

    def build(self, quick_make = False):
        self.runner(self.build_dir, "make"+EXTRA_MAKE_OPTIONS)
        if quick_make:
            return True
        self.runner(self.build_dir, "make test"+EXTRA_MAKE_OPTIONS)
        if BUILD_ALL_DOCS:
            self.runner(self.build_dir, "make doc"+EXTRA_MAKE_OPTIONS)
        # no problems found
        self.write_good_commit()

    def regtest_baseline(self):
        self.runner(self.build_dir, "make test-baseline"+EXTRA_MAKE_OPTIONS)

    def regtest_check(self):
        self.runner(self.build_dir, "make check"+EXTRA_MAKE_OPTIONS)

    def regtest_clean(self):
        a=self.runner(self.build_dir, "make test-clean"+EXTRA_MAKE_OPTIONS)

    def make_regtest_show_script(self,issue_id):
        script_filename = os.path.join(self.auto_compile_dir,
            "show-regtests-%s.sh" % (issue_id))
        out = open(script_filename, 'w')
        out.write("firefox %s\n" % os.path.join(
            self.build_dir, "show-%i/test-results/index.html" % issue_id))
        out.close()

    def copy_regtests(self, issue_id):
        shutil.copytree(
            os.path.join(self.build_dir, "out/test-results/"),
            os.path.join(self.build_dir, "show-%i/test-results/" % issue_id))

def main(patches = None):
    autoCompile = AutoCompile()
    #autoCompile.debug()
    autoCompile.prep()
    if not patches:
        autoCompile.build()
    else:
        autoCompile.build(quick_make=True)
        autoCompile.regtest_baseline()
        for patch in patches:
            issue_id = patch[0]
            patch_filename = patch[1]
            print "Trying %i with %s" % (issue_id, patch_filename)
            try:
                autoCompile.patch(patch_filename)
                autoCompile.build(quick_make=True)
                autoCompile.regtest_check()
                autoCompile.copy_regtests(issue_id)
                autoCompile.make_regtest_show_script(issue_id)
                # reverse stuff
                status = autoCompile.patch(patch_filename, reverse=True)
                autoCompile.regtest_clean()
            except:
                print "Problem with issue %i" % issue_id


if __name__ == "__main__":
    main()


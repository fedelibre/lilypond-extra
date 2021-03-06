#!/usr/bin/env python

import sys
import re
import os
import glob
import traceback
TRACEBACK_LIMIT = 40

import urllib2
import json

# API docs:
#   http://code.google.com/p/support/wiki/IssueTrackerAPIPython
import gdata.projecthosting.client
import gdata.projecthosting.data
import gdata.gauth
import gdata.client
import gdata.data
import atom.http_core
import atom.core

import compile_lilypond_test
from compile_lilypond_test import info, warn, error, config, cache

patches_dirname = os.path.expanduser (config.get ("patches", "directory"))
if (os.path.isdir (patches_dirname) and
    config.getboolean ("patches", "clean_dir_at_startup")):
    [os.remove (f)
     for f in (glob.glob (os.path.join (patches_dirname, "issue*.diff")))]
if not os.path.exists (patches_dirname):
    os.makedirs (patches_dirname)

class CodeReviewIssue (object):
    def __init__ (self, url, tracker_id, title=""):
        self.url = url
        for url_base in self.url_bases:
            if url.startswith (url_base):
                self.url_base = url_base
                break
        self.tracker_id = tracker_id
        self.title = title.encode ("ascii", "ignore")
        self.id = url.replace (self.url_base, "").strip ("/")
    def format_id (self, s):
        return s.replace (".", "_").replace ("/", "_")
    def apply_patch_commands (self):
        if self.patch_method == "file":
            return ("git apply --index %s" % self.patch_filename,)
        elif self.patch_method == "rebase":
            branch_name = "%s/%s" % (self.branch_prefix,
                                     self.id)
            return ("git fetch local %s:%s" % (branch_name, branch_name),
                    "git branch -f baseline",
                    "git checkout %s" % branch_name,
                    "git rebase baseline")
        else:
            raise NotImplementedError
    def unapply_patch_commands (self):
        if self.patch_method == "file":
            return ("git reset --hard",)
        elif self.patch_method == "rebase":
            return ("git checkout baseline",)
        else:
            raise NotImplementedError

class RietveldIssue (CodeReviewIssue):
    # Valid Rietveld urls
    url_bases = ("http://codereview.appspot.com/",
                 "https://codereview.appspot.com/")
    patch_method = "file"
    def get_patch (self):
        api_url = os.path.join (self.url_base, "api", self.id)
        request = urllib2.Request (api_url)
        response = urllib2.urlopen (request).read ()
        riet_issue_json = json.loads (response)
        patchset = riet_issue_json["patchsets"][-1]
        patch_filename = "issue%s_%s.diff" % (self.id, patchset)
        patch_url = os.path.join (self.url_base, "download", patch_filename)
        request = urllib2.Request (patch_url)
        try:
            response = urllib2.urlopen (request).read ()
        except urllib2.HTTPError:
            # Retrieving the patch failed (patch may be too large, see
            # http://code.google.com/p/rietveld/issues/detail?id=196).
            # Try to download individual patches for each file instead,
            # and concatenate them to obtain the complete patch.
            api_url2 = os.path.join (api_url, str (patchset))
            request2 = urllib2.Request (api_url2)
            response2 = urllib2.urlopen (request2).read ()
            riet_patchset_json = json.loads (response2)
            response = ""
            for file in riet_patchset_json["files"].values ():
                file_id = file["id"]
                file_patch_filename = "issue%s_%s_%s.diff" % (self.id, patchset, file_id)
                file_patch_url = os.path.join (self.url_base, "download", file_patch_filename)
                file_request = urllib2.Request (file_patch_url)
                response += urllib2.urlopen (file_request).read ()
        patch_filename_full = os.path.abspath (
            os.path.join (patches_dirname, patch_filename))
        patch_file = open (patch_filename_full, 'w')
        patch_file.write (response)
        patch_file.close ()
        self.patch_filename = patch_filename_full
        self.patch_id = self.format_id (patch_filename)
        return patch_filename_full

codereview_url_map = dict ()
for T in (RietveldIssue,):
    codereview_url_map.update (dict ((url_base, T)
                                     for url_base in T.url_bases))
codereview_url_re = re.compile (
    "((?:" + "|".join (codereview_url_map.keys ()) + r').*?)(?:<|>|\s|"|$)')

class replacementClient (gdata.projecthosting.client.ProjectHostingClient):
    "A replacement client that fetches data using csv queries."

    def get_issues (self, projectname, query=None):
        squery = ""
        if query is not None:
            if query.text_query:
                squery = query.text_query
            elif query.issue_id:
                squery = "id:%s" % query.issue_id
            elif query.label:
                squery = str (query.label).lower ().replace ("-", ":")
        surl = "http://code.google.com/p/%s/issues/csv?q=%s&colspec=ID" % (projectname, squery)
        f = urllib2.urlopen (surl)
        csv = f.readlines ()[1:]
        f.close ()
        issues = [int (s.strip ('",\n ')) for s in csv if s != '\n']
        print 'issues', issues
        return issues

class PatchBot (object):
    client = replacementClient ()

    # you can use mewes for complete junk testing
    #PROJECT_NAME = "mewes"
    PROJECT_NAME = "lilypond"

    username = None
    password = None

    def __init__ (self):
        # both of these bail if they fail
        self.get_credentials ()
        self.login ()

    def get_credentials (self):
        # TODO: can we use the coderview cookie for this?
        #filename = os.path.expanduser("~/.codereview_upload_cookies")
        filename = os.path.expanduser("~/.lilypond-project-hosting-login")
        try:
            login_data = open (filename).readlines ()
            self.username = login_data[0]
            self.password = login_data[1]
        except:
            import getpass
            warn ("""could not find stored credentials
  %(filename)s
Please enter loging details manually
""" % locals ())
            print "Username (google account name):"
            self.username = raw_input ().strip ()
            self.password = getpass.getpass ()

    def login (self):
        try:
            self.client.client_login (
                self.username, self.password,
                source='lilypond-patch-handler', service='code')
        except:
            error ("incorrect username or password")
            sys.exit (1)

    def update_issue (self, issue_id, description):
        issue = self.client.update_issue (
                self.PROJECT_NAME,
                issue_id,
                self.username,
                comment = description,
                labels = ["Patch-new"])
        return issue

    def get_issue (self, issue_id):
        query = gdata.projecthosting.client.Query (issue_id=issue_id)
        feed = self.client.get_issues (self.PROJECT_NAME, query=query)
        return feed

    def get_issues (self, issues_id):
        query = gdata.projecthosting.client.Query (
            text_query='id:%s' % ','.join (map (str, issues_id)))
        feed = self.client.get_issues (self.PROJECT_NAME,
            query=query)
        return feed

    def get_review_patches (self):
        query = gdata.projecthosting.client.Query (
            canned_query='open',
            label='Patch-review')
        feed = self.client.get_issues (self.PROJECT_NAME,
            query=query)
        return feed

    def get_new_patches (self):
        query = gdata.projecthosting.client.Query (
            canned_query='open',
            label='Patch-new')
        feed = self.client.get_issues (self.PROJECT_NAME,
            query=query)
        return feed

    def get_testable_issues (self):
        query = gdata.projecthosting.client.Query (
            text_query='Patch=new,review,needs_work,countdown status:New,Accepted,Started modified-after:today-30',
            max_results=50)
        feed = self.client.get_issues (self.PROJECT_NAME,
            query=query)
        return feed

    def do_countdown (self):
        issues = self.get_review_patches ()
        for i, issue_id in enumerate (issues):
            print i, '\t', issue_id

    def get_codereview_issue_from_issue_tracker (self, issue_id):
        "Scrape the issue page for codereview links."
        surl = "http://code.google.com/p/%s/issues/detail?id=%d" % (self.PROJECT_NAME, issue_id)
        f = urllib2.urlopen (surl)
        page_source = f.readlines ()
        f.close ()
        for line in page_source[::-1]:
            if not line:
                continue
            urls = codereview_url_re.findall (line.encode ("ascii", "ignore"))
            if len (urls) > 0:
                for u in codereview_url_map:
                    if urls[0].startswith (u):
                        print 'Found url:', urls[0]
                        return codereview_url_map[u] (urls[0], issue_id)
                else:
                    raise Exception ("Failed to match codereview URL handler")
        raise Exception ("Failed to match codereview URL handler")

    def do_new_check (self):
        issues = self.get_new_patches ()
        return self.do_check (issues)

    def do_check (self, issues):
        if not issues:
            return
        patches = []
        tests_results_dir = config.get (
            "server", "tests_results_install_dir")
        for issue_id in issues:
            info ("Trying issue %i" % issue_id)
            try:
                codereview_issue = self.get_codereview_issue_from_issue_tracker (issue_id)
                change_reference = codereview_issue.get_patch ()
            except Exception, e:
                warn ("something went wrong; omitting patch for issue %i" % issue_id)
                codereview_issue = None
            if codereview_issue:
                info ("Found patch: %s" % ",".join (str (x) for x in (
                            issue_id, change_reference, codereview_issue.title)))
                if (cache.has_section (str (issue_id))
                    and cache.has_option (
                        str (issue_id), codereview_issue.patch_id)
                    and (tests_results_dir
                         or cache.get (
                            str (issue_id),
                            codereview_issue.patch_id).lower () == "testing")):
                    info (("Last patch for issue %i already tested or under testing\n" +
                           "by another Patchy instance, skipping.") % issue_id)
                    continue
                if not cache.has_section (str (issue_id)):
                    cache.add_section (str (issue_id))
                cache.set (str (issue_id), codereview_issue.patch_id, "testing")
                cache.save ()
                patches.append (codereview_issue)
        if len (patches) > 0:
            info ("Fetching, cloning, compiling master.")
            try:
                autoCompile = compile_lilypond_test.AutoCompile ("master")
                autoCompile.build_branch (patch_prepare=True)
                if config.getboolean (
                    "compiling", "patch_test_build_docs"):
                    autoCompile.clean ("master", target="doc")
            except Exception as err:
                error ("problem compiling master. Patchy cannot reliably continue.")
                autoCompile.remove_test_master_lock ()
                raise err
            baseline_build_summary = autoCompile.logfile.log_record
            for patch in patches:
                issue_id = patch.tracker_id
                autoCompile.logfile.log_record = ""
                info ("Issue %i: %s" % (issue_id, patch.title))
                info ("Issue %i: Testing patch %s" % (issue_id, patch.patch_id))
                try:
                    results_url = autoCompile.test_issue (issue_id, patch)
                    issue_pass = True
                except Exception as err:
                    error ("issue %i: Problem encountered" % issue_id)
                    info (traceback.format_exc (TRACEBACK_LIMIT))
                    issue_pass = False
                    try:
                        results_url = autoCompile.copy_build_tree (issue_id)
                    except:
                        warn (traceback.format_exc (TRACEBACK_LIMIT))
                        pass
                if config.get ("server", "tests_results_install_dir"):
                    message = baseline_build_summary + "\n" + autoCompile.logfile.log_record
                    if "results_url" in locals ():
                        message = ("Build results are available at\n\n%s\n\n"
                                   % results_url) + message
                    self.client.update_issue (
                            self.PROJECT_NAME,
                            issue_id,
                            self.username,
                            comment = message)
                info ("Issue %i: Cleaning up" % issue_id)
                cache.set (str (issue_id), patch.patch_id, issue_pass)
                cache.save ()
                try:
                    autoCompile.cleanup_issue (issue_id, patch)
                except Exception as err:
                    error ("problem cleaning up after issue %i" % issue_id)
                    error ("Patchy cannot reliably continue.")
                    info (traceback.format_exc (TRACEBACK_LIMIT))
                    autoCompile.remove_test_master_lock ()
                    raise err
                info ("Issue %i: Done." % issue_id)
            autoCompile.remove_test_master_lock ()
        else:
            info ("No new patches to test")

    def accept_patch (self, issue_id, reason):
        issue = self.client.update_issue (
                self.PROJECT_NAME,
                issue_id,
                self.username,
                comment = "Patchy the autobot says: passes tests.  " + reason,
                labels = ["Patch-review"])
        return issue

    def reject_patch (self, issue_id, reason):
        issue = self.client.update_issue(
                self.PROJECT_NAME,
                issue_id,
                self.username,
                comment = "Patchy the autobot says: " + reason,
                labels = ["Patch-needs_work"])
        return issue


def test_countdown ():
    patchy = PatchBot ()
    patchy.do_countdown ()

def test_last_comment ():
    # Testing issue 504 which currently has 59 comments
    issue_id = "504"
    patchy = PatchBot ()
    comments_feed = patchy.client.get_comments ("lilypond", issue_id)
    def get_entry_id (entry):
        split = entry.get_id ().split ("/")
        last = split[-1]
        return int (last)
    comments_entries = list (comments_feed.entry)
    comments_entries.sort (key=get_entry_id)
    last_comment = comments_entries[-1]
    print 'last comment on issue', issue_id, 'is comment no.', get_entry_id (last_comment)

def test_new_patches ():
    patchy = PatchBot ()
    patchy.do_new_check ()

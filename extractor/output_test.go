package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestReportPublicationPreservesEvidence(t *testing.T) {
	path := filepath.Join(t.TempDir(), "report.json")
	saveReport(path, report{Complete: true})
	before, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	mustFail(t, func() { saveReport(path, report{Complete: false}) })
	after, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	if string(before) != string(after) {
		t.Fatal("existing evidence replaced")
	}
	if _, e = os.Stat(path + ".partial"); !os.IsNotExist(e) {
		t.Fatal("partial file not cleaned")
	}
}
func TestCanceledEmptyStateHasNoCompletionMarker(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	db := &database{ctx: ctx}
	r := report{Shard: 1, Cutoff: cutoff{Root: hx(emptyTrie)}, Counts: map[string]uint64{}, Accounts: map[string]map[string]string{}}
	out := filepath.Join(t.TempDir(), "state")
	mustFail(t, func() { fullState(db, &r, out, nil) })
	if _, e := os.Stat(filepath.Join(out, "summary.json")); !os.IsNotExist(e) {
		t.Fatal("canceled scan marked complete")
	}
}
func FuzzDecodeRejectsMalformedWithoutRuntimePanic(f *testing.F) {
	for _, v := range [][]byte{{0x80}, {0xc0}, {0xff}, {0xb8, 0x38}, {0xc1, 0x80}} {
		f.Add(v)
	}
	f.Fuzz(func(t *testing.T, b []byte) {
		defer func() {
			if p := recover(); p != nil {
				if _, ok := p.(auditError); !ok {
					t.Fatalf("unexpected panic: %v", p)
				}
			}
		}()
		decode(b)
	})
}

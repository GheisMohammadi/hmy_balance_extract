package main

import (
	"bytes"
	"context"
	"encoding/csv"
	"encoding/json"
	"github.com/syndtr/goleveldb/leveldb"
	"os"
	"path/filepath"
	"testing"
)

func TestFullStateReadOnlyAndUnresolved(t *testing.T) {
	for _, known := range []bool{false, true} {
		dir := t.TempDir()
		path := filepath.Join(dir, "db")
		w, e := leveldb.OpenFile(path, nil)
		if e != nil {
			t.Fatal(e)
		}
		a := bytes.Repeat([]byte{7}, 20)
		key := keccak(a)
		value := rlpList(rlpNum(2), rlpNum(123), rlpBytes(emptyTrie), rlpBytes(keccak(nil)))
		leaf := rlpList(rlpBytes(compact(nibbles(key), true)), rlpBytes(value))
		root := keccak(leaf)
		if e = w.Put(root, leaf, nil); e != nil {
			t.Fatal(e)
		}
		if known {
			if e = w.Put(append([]byte("secure-key-"), key...), a, nil); e != nil {
				t.Fatal(e)
			}
		}
		w.Close()
		db, closeDB := openDB(context.Background(), path, 1)
		if e = db.Put([]byte("forbidden"), []byte("write"), nil); e != leveldb.ErrReadOnly {
			t.Fatalf("write returned %v", e)
		}
		r := report{Shard: 1, Cutoff: cutoff{Root: hx(root)}, Accounts: map[string]map[string]string{}, Counts: map[string]uint64{}}
		out := filepath.Join(dir, "output")
		fullState(db, &r, out, nil)
		closeDB()
		f, e := os.Open(filepath.Join(out, "liquid.csv"))
		if e != nil {
			t.Fatal(e)
		}
		rows, e := csv.NewReader(f).ReadAll()
		f.Close()
		if e != nil {
			t.Fatal(e)
		}
		if len(rows) != 2 || rows[1][2] != "123" || (rows[1][1] != "") != known {
			t.Fatal(rows)
		}
		b, e := os.ReadFile(filepath.Join(out, "summary.json"))
		if e != nil {
			t.Fatal(e)
		}
		var s map[string]any
		if e = json.Unmarshal(b, &s); e != nil {
			t.Fatal(e)
		}
		if s["liquid_atto"] != "123" || s["database_read_only"] != true || s["complete_migration_balances"] != false {
			t.Fatal(s)
		}
	}
}
func TestCandidateAmountsIgnored(t *testing.T) {
	p := filepath.Join(t.TempDir(), "hints.csv")
	a := bytes.Repeat([]byte{9}, 20)
	if e := os.WriteFile(p, []byte("address,balance_atto\n"+hx(a)+",not-an-amount\n"), 0600); e != nil {
		t.Fatal(e)
	}
	ids := newIdentities()
	loadCandidates(&database{ctx: context.Background()}, ids, []string{p})
	if v, ok := ids.addresses[hashArray(keccak(a))]; !ok || !bytes.Equal(v[:], a) {
		t.Fatal("identity not discovered")
	}
}

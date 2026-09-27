package main

import (
	"bytes"
	"encoding/hex"
	"testing"
)

type memoryDB map[string][]byte

func (m memoryDB) get(k []byte) ([]byte, bool) { v, ok := m[string(k)]; return v, ok }
func mustFail(t *testing.T, f func()) {
	t.Helper()
	defer func() {
		p := recover()
		if _, ok := p.(auditError); !ok {
			t.Fatalf("expected auditError, got %v", p)
		}
	}()
	f()
}
func TestRLPVectors(t *testing.T) {
	for _, s := range []string{"80", "00", "7f", "83646f67", "c88363617483646f67", "c0"} {
		b, _ := hex.DecodeString(s)
		x := decode(b)
		if !bytes.Equal(x.raw, b) {
			t.Fatal(s)
		}
	}
	for _, s := range []string{"", "8100", "b80100", "b90038", "c181", "8000", "f80180"} {
		b, _ := hex.DecodeString(s)
		mustFail(t, func() { decode(b) })
	}
	mustFail(t, func() { integer(decode([]byte{0})) })
	if hx(emptyTrie) != "0x56e81f171bcc55a6ff8345e692c0f86e5b48e01b996cadc001622fb5e363b421" {
		t.Fatal("wrong Keccak")
	}
}
func storeNode(m memoryDB, raw []byte) []byte { h := keccak(raw); m[string(h)] = raw; return h }
func TestTrieLookupAbsenceCorruptionAndWalk(t *testing.T) {
	m := memoryDB{}
	key := keccak(bytes.Repeat([]byte{7}, 20))
	value := rlpList(rlpNum(1), rlpNum(1234567), rlpBytes(emptyTrie), rlpBytes(keccak(nil)))
	leaf := rlpList(rlpBytes(compact(nibbles(key), true)), rlpBytes(value))
	root := storeNode(m, leaf)
	tr := patricia{m, root}
	got, ok := tr.get(key)
	if !ok || !bytes.Equal(got, value) {
		t.Fatal("lookup")
	}
	other := append([]byte{}, key...)
	other[0] ^= 1
	if _, ok := tr.get(other); ok {
		t.Fatal("false inclusion")
	}
	count := 0
	tr.walk(func(k, v []byte) {
		count++
		if !bytes.Equal(k, key) || !bytes.Equal(v, value) {
			t.Fatal("walk")
		}
	})
	if count != 1 {
		t.Fatal(count)
	}
	m[string(root)] = []byte{0xc0}
	mustFail(t, func() { tr.get(key) })
	delete(m, string(root))
	mustFail(t, func() { tr.get(key) })
}
func TestInlineBranchExtensionAndReceiptTrie(t *testing.T) {
	// Independent hand-built branch, with both embedded and hashed child forms.
	m := memoryDB{}
	branch := make([][]byte, 17)
	for i := range branch {
		branch[i] = rlpBytes(nil)
	}
	short := rlpList(rlpBytes(compact([]byte{2}, true)), rlpBytes([]byte("a")))
	longValue := bytes.Repeat([]byte{3}, 40)
	long := rlpList(rlpBytes(compact([]byte{4}, true)), rlpBytes(longValue))
	branch[1] = short
	branch[3] = rlpBytes(storeNode(m, long))
	br := rlpList(branch...)
	ext := rlpList(rlpBytes(compact([]byte{10, 11}, false)), rlpBytes(storeNode(m, br)))
	tr := patricia{m, storeNode(m, ext)}
	for _, tc := range []struct{ k, v []byte }{{[]byte{0xab, 0x12}, []byte("a")}, {[]byte{0xab, 0x34}, longValue}} {
		v, ok := tr.get(tc.k)
		if !ok || !bytes.Equal(v, tc.v) {
			t.Fatal("inline/hash extension")
		}
	}
	if _, ok := tr.get([]byte{0xab, 0x56}); ok {
		t.Fatal("branch absence")
	}
	// A one-item receipt trie has the RLP(index=0) path 0x80.
	x := decode(rlpList(rlpNum(42)))
	expected := keccak(rlpList(rlpBytes([]byte{0x20, 0x80}), rlpBytes(x.raw)))
	if !bytes.Equal(listRoot([]item{x}), expected) {
		t.Fatal("ordered trie index encoding")
	}
	xs := []item{x, decode(rlpList(rlpNum(43))), decode(rlpList(rlpNum(44)))}
	rows := []pair{}
	for i, x := range xs {
		rows = append(rows, pair{nibbles(rlpNum(uint64(i))), x.raw})
	}
	raw := buildTrie(rows, 0) // hashed descendants need materializing for a large trie; these nodes are small.
	m2 := memoryDB{}
	tr2 := patricia{m2, storeNode(m2, raw)}
	for i, x := range xs {
		v, ok := tr2.get(rlpNum(uint64(i)))
		if !ok || !bytes.Equal(v, x.raw) {
			t.Fatal("receipt trie lookup")
		}
	}
}
func TestMalformedTrieRejected(t *testing.T) {
	for _, raw := range [][]byte{rlpList(), rlpList(rlpBytes([]byte{0x40}), rlpBytes(nil)), rlpList(rlpBytes([]byte{0}), rlpBytes(bytes.Repeat([]byte{1}, 32)))} {
		m := memoryDB{}
		tr := patricia{m, storeNode(m, raw)}
		mustFail(t, func() { tr.get([]byte{0}) })
	}
}

package main

import (
	"bytes"
	"encoding/binary"
	"testing"
)

func TestOutgoingRootAndMissingGroup(t *testing.T) {
	addr := bytes.Repeat([]byte{2}, 20)
	hash := bytes.Repeat([]byte{3}, 32)
	x := rlpList(rlpBytes(bytes.Repeat([]byte{4}, 32)), rlpBytes(addr), rlpBytes(addr), rlpNum(0), rlpNum(1), rlpNum(500))
	group := rlpList(x)
	root := keccak(append([]byte{0, 0, 0, 1}, listRoot(decode(group).children)...))
	key := append([]byte("cxReceipt"), make([]byte, 12)...)
	binary.BigEndian.PutUint32(key[9:13], 1)
	binary.BigEndian.PutUint64(key[13:21], 42)
	key = append(key, hash...)
	m := memoryDB{string(key): group}
	r := report{Shard: 0, Accounts: map[string]map[string]string{hx(addr): {}}, Counts: map[string]uint64{}}
	outgoingAt(m, &r, 42, hash, header{out: root})
	if len(r.Outgoing) != 1 || r.Outgoing[0].Amount != "500" {
		t.Fatal(r)
	}
	delete(m, string(key))
	mustFail(t, func() { outgoingAt(m, &r, 42, hash, header{out: root}) })
}
func syntheticHeader(number, shard uint64, root, out, in []byte) []byte {
	f := make([][]byte, 24)
	for i := range f {
		f[i] = rlpBytes(nil)
	}
	f[0] = rlpBytes(make([]byte, 32))
	f[2] = rlpBytes(root)
	f[5] = rlpBytes(out)
	f[6] = rlpBytes(in)
	f[8] = rlpNum(number)
	f[16] = rlpNum(shard)
	return rlpList(rlpBytes([]byte("HmnyTgd")), rlpBytes([]byte("v3")), rlpList(f...))
}
func TestPinnedCutoffRejectsWrongRootAndCorruption(t *testing.T) {
	raw := syntheticHeader(42, 0, emptyTrie, emptyTrie, emptyTrie)
	hash := keccak(raw)
	m := memoryDB{string(numbered('h', 42, []byte{'n'})): hash, string(numbered('h', 42, hash)): raw}
	c := cutoff{42, hx(hash), hx(emptyTrie)}
	verifyCutoff(m, c, 0)
	mustFail(t, func() { verifyCutoff(m, c, 1) })
	c.Root = hx(make([]byte, 32))
	mustFail(t, func() { verifyCutoff(m, c, 0) })
	m[string(numbered('h', 42, hash))] = []byte{0xc0}
	mustFail(t, func() { readHeader(m, 42, hash) })
}
func TestIncomingProofVerifiedAndCorruptionRejected(t *testing.T) {
	addr := bytes.Repeat([]byte{2}, 20)
	tx := bytes.Repeat([]byte{4}, 32)
	x := rlpList(rlpBytes(tx), rlpBytes(addr), rlpBytes(addr), rlpNum(0), rlpNum(1), rlpNum(500))
	group := rlpList(x)
	groupRoot := listRoot(decode(group).children)
	out := keccak(append([]byte{0, 0, 0, 1}, groupRoot...))
	src := syntheticHeader(42, 0, emptyTrie, out, emptyTrie)
	merkle := rlpList(rlpNum(42), rlpBytes(keccak(src)), rlpNum(0), rlpBytes(out), rlpList(rlpNum(1)), rlpList(rlpBytes(groupRoot)))
	proof := rlpList(group, merkle, src, rlpBytes(nil), rlpBytes(nil))
	proofs := rlpList(proof)
	body := rlpList(rlpBytes([]byte("HmnyTgd")), rlpBytes([]byte("v2")), rlpList(rlpList(), rlpList(), rlpList(), proofs))
	hash := bytes.Repeat([]byte{6}, 32)
	m := memoryDB{string(numbered('b', 43, hash)): body}
	r := report{Shard: 1, Accounts: map[string]map[string]string{hx(addr): {}}, Counts: map[string]uint64{}}
	h := header{in: listRoot(decode(proofs).children)}
	incomingAt(m, &r, 43, hash, h)
	if len(r.Incoming) != 1 || r.Incoming[0].ReceivedBlock != 43 {
		t.Fatal(r)
	}
	h.in = make([]byte, 32)
	mustFail(t, func() { incomingAt(m, &r, 43, hash, h) })
}

// Component-level oracle for the setup described in Headscale issue #2121.
// The same fixture is applied to the official repair commit and its parent.
package db

import (
	"testing"

	"github.com/juanfont/headscale/hscontrol/types"
	"gorm.io/gorm"
	"tailscale.com/types/key"
)

func TestResearchRename2121(t *testing.T) {
	for _, scenario := range []struct {
		name      string
		request   string
		wantName  string
		wantError bool
	}{
		{"unique_name", "workstation", "workstation", false},
		{"duplicate_name", "laptop", "server", true},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			store, err := newTestDB()
			if err != nil {
				t.Fatal(err)
			}
			alice, err := store.CreateUser("alice")
			if err != nil {
				t.Fatal(err)
			}
			bob, err := store.CreateUser("bob")
			if err != nil {
				t.Fatal(err)
			}
			nodes := []types.Node{
				{ID: 1, UserID: alice.ID, Hostname: "laptop", GivenName: "laptop",
					MachineKey: key.NewMachine().Public(), NodeKey: key.NewNode().Public()},
				{ID: 2, UserID: bob.ID, Hostname: "server", GivenName: "server",
					MachineKey: key.NewMachine().Public(), NodeKey: key.NewNode().Public()},
			}
			for i := range nodes {
				if err := store.DB.Save(&nodes[i]).Error; err != nil {
					t.Fatal(err)
				}
			}
			err = store.Write(func(tx *gorm.DB) error {
				// An untyped ID literal is accepted by both historical signatures.
				return RenameNode(tx, 2, scenario.request)
			})
			if scenario.wantError && err == nil {
				t.Error("duplicate-name rename was accepted")
			}
			if !scenario.wantError && err != nil {
				t.Fatal(err)
			}
			got, err := store.ListNodes()
			if err != nil {
				t.Fatal(err)
			}
			if len(got) != 2 {
				t.Fatalf("got %d nodes, want 2", len(got))
			}
			names := make(map[string]bool)
			for _, node := range got {
				if names[node.GivenName] {
					t.Errorf("duplicate given name persisted: %s", node.GivenName)
				}
				names[node.GivenName] = true
				if node.ID == 2 && node.GivenName != scenario.wantName {
					t.Errorf("node 2 name = %q, want %q", node.GivenName, scenario.wantName)
				}
			}
		})
	}
}

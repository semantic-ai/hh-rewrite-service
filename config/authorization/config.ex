alias Acl.Accessibility.Always, as: AlwaysAccessible
alias Acl.GraphSpec.Constraint.Resource, as: ResourceConstraint
alias Acl.GraphSpec, as: GraphSpec
alias Acl.GroupSpec, as: GroupSpec

defmodule Acl.UserGroups.Config do
  def user_groups do
    [
      %GroupSpec{
        name: "rewrite-all",
        useage: [:read, :write],
        access: %AlwaysAccessible{},
        graphs: [
          %GraphSpec{
            graph: "http://mu.semte.ch/graphs/public",
            constraint: %ResourceConstraint{
              resource_types: [
                "http://mu.semte.ch/vocabularies/ext/Guideline",
                "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#InstancePublicService",
                "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#Procedure",
                "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#FieldGeneration",
                "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#GenerationAttempt",
                "http://www.w3.org/ns/prov#Activity",
                "http://redpencil.data.gift/vocabularies/tasks/Task",
                "http://mu.semte.ch/vocabularies/ext/AICall"
              ]
            }
          }
        ]
      }
    ]
  end
end

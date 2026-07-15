using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;
using System.Text.Json;

if (args.Length != 2 || args[0] != "--file")
{
    Console.Error.WriteLine("Usage: dotnet-quality-roslyn --file <path>");
    return 2;
}

var filePath = Path.GetFullPath(args[1]);
if (!File.Exists(filePath))
{
    Console.Error.WriteLine($"C# file not found: {filePath}");
    return 1;
}

var source = await File.ReadAllTextAsync(filePath);
var tree = CSharpSyntaxTree.ParseText(source, path: filePath);
var root = await tree.GetRootAsync();
var types = root.DescendantNodes()
    .OfType<BaseTypeDeclarationSyntax>()
    .Where(IsTopLevelType)
    .Select(type => CreateTypeInfo(type, tree))
    .ToArray();

var diagnostics = tree.GetDiagnostics()
    .Where(diagnostic => diagnostic.Severity == DiagnosticSeverity.Error)
    .Select(diagnostic => new DiagnosticInfo(
        diagnostic.Id,
        diagnostic.GetMessage(),
        tree.GetLineSpan(diagnostic.Location.SourceSpan).StartLinePosition.Line + 1))
    .ToArray();

var result = new FileAnalysis(types, diagnostics);
Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions
{
    PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
}));
return 0;

static bool IsTopLevelType(BaseTypeDeclarationSyntax type)
{
    return type.Parent is CompilationUnitSyntax
        or NamespaceDeclarationSyntax
        or FileScopedNamespaceDeclarationSyntax;
}

static TypeInfo CreateTypeInfo(BaseTypeDeclarationSyntax type, SyntaxTree tree)
{
    var kind = type switch
    {
        ClassDeclarationSyntax => "class",
        InterfaceDeclarationSyntax => "interface",
        StructDeclarationSyntax => "struct",
        RecordDeclarationSyntax => "record",
        EnumDeclarationSyntax => "enum",
        _ => "type",
    };

    var line = tree.GetLineSpan(type.Span).StartLinePosition.Line + 1;
    var methods = new HashSet<string>(StringComparer.Ordinal);
    var methodInfos = new List<MethodInfo>();
    var exposedMethods = new HashSet<string>(StringComparer.Ordinal);
    var targetableMembers = new HashSet<string>(StringComparer.Ordinal);

    var members = type is TypeDeclarationSyntax declaration
        ? declaration.Members
        : [];
    foreach (var member in members)
    {
        switch (member)
        {
            case MethodDeclarationSyntax method:
                methods.Add(method.Identifier.ValueText);
                targetableMembers.Add(method.Identifier.ValueText);
                methodInfos.Add(new MethodInfo(
                    method.Identifier.ValueText,
                    tree.GetLineSpan(method.Span).StartLinePosition.Line + 1,
                    IsTestMethod(method)));
                if (IsExposed(method.Modifiers))
                {
                    exposedMethods.Add(method.Identifier.ValueText);
                }
                break;
            case ConstructorDeclarationSyntax:
                targetableMembers.Add("Constructor");
                break;
            case PropertyDeclarationSyntax property:
                targetableMembers.Add(property.Identifier.ValueText);
                targetableMembers.Add("Properties");
                break;
            case EventDeclarationSyntax eventDeclaration:
                targetableMembers.Add(eventDeclaration.Identifier.ValueText);
                targetableMembers.Add("Events");
                break;
            case EventFieldDeclarationSyntax eventField:
                foreach (var variable in eventField.Declaration.Variables)
                {
                    targetableMembers.Add(variable.Identifier.ValueText);
                }
                targetableMembers.Add("Events");
                break;
        }
    }

    var baseTypes = type.BaseList?.Types
        .Select(baseType => baseType.Type.ToString())
        .Select(RemoveGenericArguments)
        .Select(value => value.Split('.').Last())
        .Where(value => value.Length > 0)
        .ToArray() ?? [];

    return new TypeInfo(
        type.Identifier.ValueText,
        kind,
        line,
        type.Modifiers.Any(SyntaxKind.PartialKeyword),
        exposedMethods,
        targetableMembers,
        kind == "class",
        baseTypes,
        methodInfos.ToArray());
}

static bool IsExposed(SyntaxTokenList modifiers)
{
    return modifiers.Any(modifier => modifier.IsKind(SyntaxKind.PublicKeyword)
        || modifier.IsKind(SyntaxKind.ProtectedKeyword)
        || modifier.IsKind(SyntaxKind.InternalKeyword));
}

static bool IsTestMethod(MethodDeclarationSyntax method)
{
    return method.AttributeLists
        .SelectMany(attributes => attributes.Attributes)
        .Select(attribute => attribute.Name.ToString().Split('.').Last())
        .Any(name => name is "Fact" or "Theory" or "SkippableFact" or "Test" or "TestMethod"
            || name.EndsWith("FactAttribute", StringComparison.Ordinal)
            || name.EndsWith("TestAttribute", StringComparison.Ordinal));
}

static string RemoveGenericArguments(string value)
{
    var genericStart = value.IndexOf('<');
    return genericStart >= 0 ? value[..genericStart] : value;
}

record FileAnalysis(TypeInfo[] Types, DiagnosticInfo[] Diagnostics);
record DiagnosticInfo(string Id, string Message, int Line);
record TypeInfo(
    string Name,
    string Kind,
    int Line,
    bool IsPartial,
    HashSet<string> ExposedMethods,
    HashSet<string> TargetableMembers,
    bool RequiresTestClass,
    string[] BaseTypes,
    MethodInfo[] Methods);
record MethodInfo(string Name, int Line, bool IsTestMethod);
